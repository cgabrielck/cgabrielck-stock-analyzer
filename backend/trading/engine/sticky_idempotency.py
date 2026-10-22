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
# accept or reject. 409/422 and other 4xx client errors are definite rejects.
UNKNOWN_HTTP_STATUSES = frozenset({408, 500, 502, 503, 504})

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
    found = {}
    memory = getattr(store, "_orders_by_id", None)
    if isinstance(memory, dict):
        for order in memory.values():
            if isinstance(order, Order):
                found[order.id] = order
    getter = getattr(store, "get_recent_orders", None)
    if callable(getter):
        try:
            rows = getter(limit=1000)
        except TypeError:
            rows = getter()
        for order in rows or []:
            if isinstance(order, Order):
                found[order.id] = order
    return list(found.values())
