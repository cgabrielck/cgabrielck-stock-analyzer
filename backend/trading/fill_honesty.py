"""Fill-price honesty (issue #10 O1–O4, O6).

A fill is only a fill when a recorded price is > 0. Limit / mark / entry /
zero / NaN never count as ``filled_avg_price``. Missing or zero fill cannot
escalate status to filled; fills stay empty.

O5 (Desk vs worker dual path) is out of scope — R1-PATH / Intent / M8 / H1.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Mapping, Optional

from backend.trading.models import Order, OrderStatus
from backend.trading.risk.gates import positive_price

_NUMERIC_FILL_TYPES = (int, float, str, Decimal)

FILL_PRICE_KEYS = ("filled_avg_price", "fill_price", "filled_price")
FILL_STATUSES = frozenset({"filled", "partially_filled"})
UNVERIFIED_FILL_STATUS = "submitted"
SHADOW_NO_POSITIVE_FILL = (
    "Shadow: no positive fill price. Cannot mark filled at $0 or from a placeholder."
)


def _field(source: Any, key: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def status_key(status: Any) -> str:
    raw = getattr(status, "value", status)
    return str(raw or "").strip().lower()


def is_fill_status(status: Any) -> bool:
    return status_key(status) in FILL_STATUSES


def recorded_fill_price(*sources: Any) -> Optional[float]:
    """Return a real fill price > 0, or None.

    Never reads ``limit_price``, ``entry_price``, mark, or stop. Journal
    ``_recorded_fill_price`` is the same contract; this helper is for the
    broker → sync → store → Desk/performance chain.
    """
    for source in sources:
        if source is None:
            continue
        for key in FILL_PRICE_KEYS:
            raw = _field(source, key)
            # Reject MagicMock / objects: float(MagicMock()) == 1.0 in CPython.
            if isinstance(raw, bool) or not isinstance(raw, _NUMERIC_FILL_TYPES):
                continue
            px = positive_price(raw)
            if px is not None:
                return px
    return None


def honest_fill_status(
    status: Any,
    *sources: Any,
    fallback: str = UNVERIFIED_FILL_STATUS,
) -> str:
    """Demote filled / partially_filled to *fallback* when no price > 0."""
    raw = status_key(status)
    if raw in FILL_STATUSES and recorded_fill_price(*sources) is None:
        return fallback
    return raw or fallback


def demote_unpriced_fill(status: OrderStatus, fill_px: Optional[float]) -> OrderStatus:
    """Order enum form of ``honest_fill_status``. Does not invent ACCEPTANCE_UNKNOWN."""
    if status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED) and fill_px is None:
        return OrderStatus.SUBMITTED
    return status


def apply_recorded_fill(order: Order, *sources: Any) -> Order:
    """Stamp only a real fill onto *order*. Missing/zero → not filled, fills empty.

    Does not convert 502-class ``ACCEPTANCE_UNKNOWN`` into a fill, and does
    not copy limit/entry into ``filled_avg_price``.
    """
    if order.status == OrderStatus.ACCEPTANCE_UNKNOWN:
        order.filled_avg_price = None
        order.filled_at = None
        return order
    fill_px = recorded_fill_price(*sources, order)
    order.filled_avg_price = fill_px
    if is_fill_status(order.status) and fill_px is None:
        order.status = OrderStatus.SUBMITTED
        order.filled_at = None
    elif fill_px is not None:
        for source in sources:
            filled_at = _field(source, "filled_at")
            if filled_at is not None:
                order.filled_at = filled_at
                break
    return order


def honest_order_row(row: Any) -> Dict[str, Any]:
    """Desk/API row: filled without a real price is not shown as filled."""
    if hasattr(row, "model_dump"):
        data = row.model_dump(mode="json")
    elif isinstance(row, Mapping):
        data = dict(row)
    else:
        data = {}
    fill = recorded_fill_price(data, row)
    data["filled_avg_price"] = fill
    data["status"] = honest_fill_status(data.get("status"), data)
    return data


def fill_notify_price(order: Any) -> Optional[float]:
    """Price for a fill notification. None → do not claim a fill."""
    return recorded_fill_price(order)
