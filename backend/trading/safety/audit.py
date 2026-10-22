"""
Audit trail — append-only immutable log of all safety decisions.

Logs every kill-switch check, mandate evaluation, and risk decision to a
time-stamped JSONL file. This is forensic: when something goes wrong, you
want to know exactly what the worker saw and decided at each step.

Inspired by Vibe-Trading's audit-first design.

AuditEvent v0 (MODULAR_ARCHITECTURE §5.4) is written alongside the historical
flat fields so existing readers keep working.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from backend.utils.constants import DATA_DIR
from backend.utils.provenance.trade_log import infer_audit_kind, normalize_audit_event

logger = logging.getLogger(__name__)

AUDIT_DIR = Path(DATA_DIR) / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

_EVENT_TYPE_KIND = {
    "risk_decision": None,  # depends on approved
    "mandate_decision": None,
    "order_submission": "order_submit",
    "order_fill": "order_fill",
    "kill_switch_check": "kill",
    "kill_switch_engage": "kill",
    "kill_switch_resume": "resume",
    "resume": "resume",
    "config_change": "config_change",
}


def _current_log_file() -> Path:
    """Return today's audit log file (one JSONL per day for easy rotation)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return AUDIT_DIR / f"audit_{today}.jsonl"


def log_event(
    event_type: str,
    details: Dict[str, Any],
    *,
    actor: str = "worker",
    kind: Optional[str] = None,
) -> Optional[str]:
    """
    Append an audit event to today's log.

    Args:
        event_type: Category (e.g. "kill_switch_check", "mandate_decision", "risk_decision")
        details:    Arbitrary dict with event specifics (symbol, approved, reason, etc.)

    Returns:
        event_id when the write succeeds, else None. Failures never raise.
    """
    try:
        event_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        payload = dict(details or {})
        resolved_kind = kind or infer_audit_kind(
            {"event_type": event_type, "kind": kind, **payload}
        ) or _EVENT_TYPE_KIND.get(event_type) or event_type
        entry = {
            "schema_version": "audit.v0",
            "event_id": event_id,
            "ts": ts,
            "actor": actor,
            "kind": resolved_kind,
            "event_type": event_type,
            "payload": payload,
            "account_id": payload.get("account_id"),
            **payload,
        }
        with open(_current_log_file(), "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        return event_id
    except Exception as exc:
        # Audit failure is logged but never blocks trading — audit is observability,
        # not control. The kill switch and mandate gate remain authoritative.
        logger.error("Audit log write failed: %s", exc)
        return None


def log_kill_switch_check(halted: bool, reason: Optional[str] = None) -> Optional[str]:
    """Log a kill switch check (called every worker tick)."""
    return log_event("kill_switch_check", {"halted": halted, "reason": reason}, kind="kill")


def log_kill_switch_engage(
    reason: Optional[str] = None,
    *,
    actor: str = "unknown",
    source: Optional[str] = None,
    previous_halted: bool = False,
    sentinel: Optional[str] = None,
) -> Optional[str]:
    """Log a kill-switch engage (control-plane halt, not a per-tick check)."""
    return log_event(
        "kill_switch_engage",
        {
            "halted": True,
            "engaged": True,
            "reason": reason,
            "source": source,
            "previous_halted": previous_halted,
            "sentinel": sentinel,
        },
        actor=actor,
        kind="kill",
    )


def log_kill_switch_resume(
    reason: Optional[str] = None,
    *,
    actor: str = "unknown",
    source: Optional[str] = None,
    sentinel: Optional[str] = None,
) -> Optional[str]:
    """Log a kill-switch resume (control-plane clear, not a per-tick check)."""
    return log_event(
        "kill_switch_resume",
        {
            "halted": False,
            "engaged": False,
            "reason": reason or "Kill switch cleared",
            "source": source,
            "previous_halted": True,
            "sentinel": sentinel,
        },
        actor=actor,
        kind="resume",
    )


def log_mandate_decision(
    symbol: str,
    side: str,
    approved: bool,
    reason: Optional[str] = None,
    notional: Optional[float] = None,
    *,
    actor: str = "worker",
) -> Optional[str]:
    """Log a mandate gate decision."""
    return log_event("mandate_decision", {
        "symbol": symbol,
        "side": side,
        "approved": approved,
        "reason": reason,
        "notional": notional,
    }, actor=actor, kind="risk_allow" if approved else "mandate_deny")


def log_risk_decision(
    symbol: str,
    side: str,
    quantity: float,
    approved: bool,
    reason: Optional[str] = None,
    *,
    actor: str = "worker",
) -> Optional[str]:
    """Log a risk engine decision."""
    return log_event("risk_decision", {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "approved": approved,
        "reason": reason,
    }, actor=actor, kind="risk_allow" if approved else "risk_reject")


def log_order_submission(
    symbol: str,
    side: str,
    quantity: float,
    limit_price: Optional[float],
    order_id: str,
    status: str,
    *,
    actor: str = "worker",
) -> Optional[str]:
    """Log an order submission result."""
    return log_event("order_submission", {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "limit_price": limit_price,
        "order_id": order_id,
        "status": status,
    }, actor=actor, kind="order_submit")


def iter_audit_files(*, days: int = 14, audit_dir: Optional[Path] = None) -> List[Path]:
    root = Path(audit_dir or AUDIT_DIR)
    if not root.exists():
        return []
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=max(0, int(days)))
    files: List[Path] = []
    for path in sorted(root.glob("audit_*.jsonl")):
        stem = path.stem.replace("audit_", "", 1)
        try:
            day = datetime.strptime(stem, "%Y-%m-%d").date()
        except ValueError:
            files.append(path)
            continue
        if day >= cutoff:
            files.append(path)
    return files


def read_recent_events(
    *,
    limit: int = 200,
    days: int = 14,
    kinds: Optional[Iterable[str]] = None,
    audit_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Read recent AuditEvent v0 rows (legacy jsonl lines are normalized)."""
    wanted = {str(k) for k in kinds} if kinds else None
    events: List[Dict[str, Any]] = []
    for path in iter_audit_files(days=days, audit_dir=audit_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = normalize_audit_event(raw)
            if not event:
                continue
            if wanted and event.get("kind") not in wanted and event.get("event_type") not in wanted:
                continue
            events.append(event)
    events.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
    return events[: max(1, int(limit))] if events else []


def get_event(event_id: str, *, days: int = 60, audit_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    if not event_id:
        return None
    for event in read_recent_events(limit=5000, days=days, audit_dir=audit_dir):
        if event.get("event_id") == event_id:
            return event
    return None
