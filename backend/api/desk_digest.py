"""Desk daily digest — JSON for FastAPI UI. Does not send Telegram or place orders.

Wave 1 Telegram post-close / 日報 and this Desk surface share
``paper_performance.build_daily_digest``. ``classify_orders`` stays here for tests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

_PENDING = {
    "new",
    "accepted",
    "pending_new",
    "pending",
    "held",
    "partially_filled",
    "open",
    "replaced",
    "submitted",
    "draft",
    "risk_approved",
}
_FILLED = {"filled"}


def _status_of(row: Mapping[str, Any]) -> str:
    raw = row.get("status")
    if hasattr(raw, "value"):
        raw = raw.value
    return str(raw or "").lower().strip()


def _symbol_of(row: Mapping[str, Any]) -> str:
    return str(row.get("symbol") or row.get("ticker") or "").upper().strip()


def _when(row: Mapping[str, Any]) -> Optional[datetime]:
    for key in ("filled_at", "updated_at", "created_at", "submitted_at"):
        raw = row.get(key)
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        except Exception:
            continue
    return None


def classify_orders(
    orders: List[Mapping[str, Any]],
    *,
    now_et: Optional[datetime] = None,
) -> Dict[str, Any]:
    now_et = now_et or datetime.now(_ET)
    today = now_et.date()
    filled: List[str] = []
    filled_today: List[str] = []
    pending: List[str] = []
    for row in orders or []:
        st = _status_of(row)
        sym = _symbol_of(row)
        if not sym:
            continue
        if st in _FILLED:
            filled.append(sym)
            when = _when(row)
            if when is not None and when.astimezone(_ET).date() == today:
                filled_today.append(sym)
        elif st in _PENDING:
            pending.append(sym)

    def uniq(seq: List[str]) -> List[str]:
        out: List[str] = []
        for s in seq:
            if s not in out:
                out.append(s)
        return out

    return {
        "filled": uniq(filled),
        "filled_today": uniq(filled_today),
        "pending": uniq(pending),
        "filled_count": len(filled),
        "filled_today_count": len(filled_today),
        "pending_count": len(pending),
        "note": "pending = new/accepted/working at broker; not a fill and not a seal.",
    }


def build_desk_digest(
    *,
    scan: Optional[Dict[str, Any]] = None,
    worker: Optional[Dict[str, Any]] = None,
    orders: Optional[List[Mapping[str, Any]]] = None,
    paper: Optional[Dict[str, Any]] = None,
    now_et: Optional[datetime] = None,
) -> Dict[str, Any]:
    """One-screen paper ops memo for Desk. Delegates to Wave 1 daily digest."""
    from backend.api.paper_performance import build_daily_digest

    now_et = now_et or datetime.now(_ET)
    order_rows: Optional[List[Dict[str, Any]]] = None
    if orders is not None:
        order_rows = [dict(row) for row in orders]
    return build_daily_digest(
        paper=paper,
        as_of_day=now_et.date(),
        orders=order_rows,
        worker_status=worker,
        scan_payload=scan,
    )
