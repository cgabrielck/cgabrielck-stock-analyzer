"""Desk journal assembly: trade_journal.json + order store + risk audit jsonl."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.api.desk_display import journal_display_audit, journal_display_row
from backend.utils.provenance import journal_store
from backend.utils.provenance.trade_log import (
    TRADE_LOG_SCHEMA,
    from_audit_event,
    looks_like_verified_fake,
    make_note_entry,
    merge_trade_logs,
)

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "Orders, risk rejects, and operator notes from the local ledger. "
    "Empty means nothing was recorded — not a live fill. "
    "Fills appear only with a recorded fill price. Streamlit journal is lab/legacy."
)
DISCLAIMER_ZH = (
    "本機帳本中的訂單、風控拒絕與操作筆記。空白代表尚未入帳，不是即時成交。"
    "只有已記錄成交價才會顯示 fill。Streamlit 日誌只是實驗室／舊介面。"
)


def _safe_orders(order_store: Any, limit: int = 200) -> List[Any]:
    if order_store is None:
        return []
    try:
        getter = getattr(order_store, "get_recent_orders", None)
        if callable(getter):
            return list(getter(limit=limit) or [])
    except Exception as exc:
        logger.warning("journal order-store read failed: %s", exc)
    return []


def _safe_audit(limit: int = 400) -> List[Dict[str, Any]]:
    try:
        from backend.trading.safety import audit

        return audit.read_recent_events(
            limit=limit,
            days=21,
            kinds=("risk_reject", "mandate_deny", "order_submit", "order_fill", "kill", "resume"),
        )
    except Exception as exc:
        logger.warning("journal audit read failed: %s", exc)
        return []


def build_journal_response(
    *,
    order_store: Any = None,
    limit: int = 100,
    kind: str = "all",
) -> Dict[str, Any]:
    raw_notes = journal_store.load_raw_entries()
    orders = _safe_orders(order_store)
    audits = _safe_audit()
    entries = merge_trade_logs(raw_notes, orders, audits, limit=limit, kind=kind)
    sources_used: List[str] = []
    if raw_notes:
        sources_used.append("trade_journal")
    if orders:
        sources_used.append("order_store")
    if audits:
        sources_used.append("audit_jsonl")
    for entry in entries:
        if looks_like_verified_fake(entry.get("provenance")):
            entry["provenance"]["verified"] = False
            entry["provenance"]["live"] = False
    entries = [journal_display_row(entry) for entry in entries]
    return {
        "schema_version": TRADE_LOG_SCHEMA,
        "entries": entries,
        "empty": len(entries) == 0,
        "empty_reason": "no_recorded_events" if not entries else None,
        "disclaimer": DISCLAIMER,
        "disclaimer_zh": DISCLAIMER_ZH,
        "sources_used": sources_used,
        "verified": False,
        "live": False,
    }


def add_note(symbol: str, note: str, side: Optional[str] = None) -> Dict[str, Any]:
    entry = make_note_entry(symbol, note, side)
    stored = journal_store.append_entry(entry)
    return stored


def record_from_order(
    order: Any,
    *,
    audit_id: Optional[str] = None,
    strategy_id: Optional[str] = None,
    intent_id: Optional[str] = None,
    link_intent: bool = True,
    provenance_extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """API facade — recording lives in utils.provenance.journal_store."""
    return journal_store.record_from_order(
        order,
        audit_id=audit_id,
        strategy_id=strategy_id,
        intent_id=intent_id,
        link_intent=link_intent,
        provenance_extra=provenance_extra,
    )


def get_entry(log_id: str, *, order_store: Any = None) -> Optional[Dict[str, Any]]:
    """Single-entry read for Desk click-through. Display overlay only — not a ledger write."""
    if not log_id:
        return None
    found = journal_store.find_entry(log_id)
    if found:
        return journal_display_row(found)
    payload = build_journal_response(order_store=order_store, limit=400, kind="all")
    for entry in payload.get("entries") or []:
        if entry.get("log_id") == log_id or entry.get("order_id") == log_id:
            return journal_display_row(entry)
    return None


def get_audit_event(event_id: str) -> Optional[Dict[str, Any]]:
    try:
        from backend.trading.safety import audit

        event = audit.get_event(event_id)
        if event:
            return event
        # Allow following a TradeLogEntry that was derived from this audit line.
        for raw in audit.read_recent_events(limit=2000, days=60):
            derived = from_audit_event(raw)
            if derived and derived.get("risk_decision_id") == event_id:
                return raw
    except Exception as exc:
        logger.warning("journal audit lookup failed: %s", exc)
    return None


def linked_audit_for_entry(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Attach a display-only audit copy. Does not write audit jsonl / journal."""
    if not entry:
        return None
    audit_id = entry.get("risk_decision_id")
    if not audit_id:
        return None
    return journal_display_audit(get_audit_event(str(audit_id)), entry=entry)
