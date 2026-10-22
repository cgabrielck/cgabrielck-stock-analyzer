"""Sticky idempotency after a 502-class place.

A gateway/timeout on place-order does not prove the broker rejected the
order. Minting a fresh idempotency key on retry is a second independent
submit. This module keeps the original key and tells callers to fail
closed instead of blind-retrying.

Safe retry: the same ``idempotency_key`` may be sent again (broker
dedupes on ``client_order_id``). A different key is not a safe retry.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

from backend.trading.engine.stamp import VENUE_STATUSES
from backend.trading.models import Order, OrderStatus

logger = logging.getLogger(__name__)

DO_NOT_BLIND_RETRY = "do_not_blind_retry"

# Statuses that mean the HTTP response (or the lack of one) does not prove
# accept or reject. Other 4xx client errors are definite rejects, except a
# 422 whose body says client_order_id is already taken: Alpaca does not
# return the original order, but the id did land.
UNKNOWN_HTTP_STATUSES = frozenset({408, 500, 502, 503, 504})

_CLIENT_ORDER_CONFLICT_TOKENS = ("unique", "duplicate", "already")

_UNKNOWN_TYPE_TOKENS = (
    "timeout",
    "connecttimeout",
    "readtimeout",
    "writetimeout",
    "connectionreset",
    "remotedisconnected",
    "protocolerror",
    "chunkedencoding",
)

_UNKNOWN_TEXT_TOKENS = (
    "bad gateway",
    "gateway timeout",
    "gateway time-out",
    "service unavailable",
    "http 502",
    "http 503",
    "http 504",
    "status 502",
    "status 503",
    "status 504",
    "status_code=502",
    "status code 502",
    "(502)",
    "(503)",
    "(504)",
)


def order_side_value(order: Order) -> str:
    side = getattr(order, "side", "")
    raw = side.value if hasattr(side, "value") else side
    return str(raw or "").lower()


class StickyLookupUnproven(RuntimeError):
    """The store cannot prove that no sticky idempotency key is open.

    A recent-N window is not proof of absence. Callers must not broker-submit
    a new key when this is raised.
    """


def message_is_client_order_id_conflict(text: Optional[str]) -> bool:
    """True when the body is Alpaca's duplicate ``client_order_id`` reject.

    Other 422s (insufficient qty, buying power) stay definite rejects.
    """
    low = str(text or "").lower()
    if "client_order_id" not in low:
        return False
    return any(token in low for token in _CLIENT_ORDER_CONFLICT_TOKENS)


def is_client_order_id_conflict(exc: BaseException) -> bool:
    """422 unique after a place that may already have landed.

    Alpaca responds ``client_order_id must be unique`` and does not hand
    back the original order. That is evidence the id was accepted, not a
    clean reject. A missing status code still matches on the body so a
    wrapped mid-retry error is not treated as a definite reject.
    """
    code = _exception_status_code(exc)
    if code is not None and code != 422:
        return False
    return message_is_client_order_id_conflict(str(exc))


def is_acceptance_unknown_exception(exc: BaseException) -> bool:
    """True when the broker/transport failure leaves acceptance unknown.

    Definite client rejects (4xx other than 408) stay False so a later
    new key is still allowed. Connection refused before a listener
    accepted the TCP session is also a definite failure.
    """
    code = _exception_status_code(exc)
    if code in UNKNOWN_HTTP_STATUSES:
        return True
    if code is not None and 400 <= code < 500:
        return False
    name = type(exc).__name__.lower()
    if any(token in name for token in _UNKNOWN_TYPE_TOKENS):
        return True
    text = str(exc).lower().strip()
    if text in {"502", "503", "504", "500", "408"}:
        return True
    return any(token in text for token in _UNKNOWN_TEXT_TOKENS)


def _exception_status_code(exc: BaseException) -> Optional[int]:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    for holder_name in ("response", "_http_error"):
        holder = getattr(exc, holder_name, None)
        if holder is None:
            continue
        response = holder if holder_name == "response" else getattr(holder, "response", None)
        code = getattr(response, "status_code", None) if response is not None else None
        if isinstance(code, int):
            return code
    return None


def acceptance_unknown_message(order: Order, exc: Optional[BaseException] = None) -> str:
    key = order.idempotency_key
    why = f" ({exc})" if exc else ""
    return (
        "Broker acceptance is unknown"
        f"{why}. Do not blind-retry with a new idempotency key. "
        f"Reuse idempotency_key={key}."
    )


def mark_acceptance_unknown(order: Order, exc: Optional[BaseException] = None) -> Order:
    """Record an unknown-accept outcome. Does not claim filled or rejected."""
    order.status = OrderStatus.ACCEPTANCE_UNKNOWN
    order.idempotency_sticky = True
    order.error_message = acceptance_unknown_message(order, exc)
    order.failed_at = None
    order.filled_at = None
    order.filled_avg_price = None
    return order


def pin_sticky(store: Any, order: Order) -> None:
    """Remember this key so a later new key cannot submit independently."""
    order.idempotency_sticky = True
    save = getattr(store, "save_order", None) if store is not None else None
    if not callable(save):
        return
    try:
        save(order)
    except Exception:
        logger.exception("Failed to pin sticky idempotency key for %s", getattr(order, "symbol", "?"))


def clear_sticky(store: Any, order: Order) -> None:
    """Drop the pin once the outcome is durable or a definite reject."""
    if not getattr(order, "idempotency_sticky", False):
        return
    order.idempotency_sticky = False
    save = getattr(store, "save_order", None) if store is not None else None
    if callable(save):
        try:
            save(order)
        except Exception:
            logger.exception("Failed to clear sticky idempotency key for %s", getattr(order, "symbol", "?"))


def is_sticky_open(order: Order) -> bool:
    if not getattr(order, "idempotency_sticky", False):
        return False
    return order.status == OrderStatus.ACCEPTANCE_UNKNOWN or order.status in VENUE_STATUSES


def find_sticky_order(store: Any, *, symbol: str, side: str) -> Optional[Order]:
    """Latest sticky open order for this symbol and side, if any."""
    want_symbol = str(symbol or "").upper()
    want_side = str(side or "").lower()
    if not want_symbol or not want_side or store is None:
        return None
    matches: List[Order] = []
    for order in _iter_orders(store):
        if not is_sticky_open(order):
            continue
        if str(getattr(order, "symbol", "") or "").upper() != want_symbol:
            continue
        if order_side_value(order) != want_side:
            continue
        matches.append(order)
    if not matches:
        return None
    matches.sort(key=lambda o: o.updated_at or o.created_at, reverse=True)
    return matches[0]


def detach_if_would_clobber_sticky(store: Any, order: Order) -> None:
    """Keep a sticky row when a gate reject would overwrite its key.

    Definite broker rejects still update the sticky row themselves.
    Gate rejects (kill / mandate / risk) must not erase the unknown order.
    """
    getter = getattr(store, "get_order_by_idempotency_key", None) if store is not None else None
    if not callable(getter):
        return
    try:
        existing = getter(order.idempotency_key)
    except Exception:
        logger.exception("Sticky lookup failed for %s", order.idempotency_key)
        return
    if existing is None or not is_sticky_open(existing):
        return
    if existing.id == order.id and existing.status == order.status:
        return
    order.idempotency_key = f"{existing.idempotency_key}:not-retry:{order.id}"
    if existing.id == order.id:
        order.id = f"{order.id}:reject"


def unknown_accept_detail(order: Order, *, journaled: bool = False) -> dict:
    key = order.idempotency_key
    return {
        "error": "acceptance_unknown",
        "retry": DO_NOT_BLIND_RETRY,
        "acceptance": "unknown",
        "message": acceptance_unknown_message(order),
        "idempotency_key": key,
        "order_id": order.id,
        "symbol": order.symbol,
        "journaled": journaled,
        "broker_submitted": None,
    }


def _iter_orders(store: Any) -> Iterable[Order]:
    """Every order the store can see. Never a recent-N window.

    ``get_recent_orders(1000)`` hides an older sticky row and lets a new
    key submit again. If the store cannot scan the whole ledger, raise
    ``StickyLookupUnproven`` so callers fail closed.
    """
    scanner = getattr(store, "iter_all_orders", None)
    if callable(scanner):
        rows = scanner()
        return [order for order in (rows or []) if isinstance(order, Order)]
    memory = getattr(store, "_orders_by_id", None)
    if isinstance(memory, dict):
        return [order for order in memory.values() if isinstance(order, Order)]
    raise StickyLookupUnproven(
        "Order store cannot prove a sticky idempotency key is absent "
        "(no full-ledger scan). Refusing to treat a recent-N window as empty."
    )
