"""
Audit trail — append-only immutable log of all safety decisions.

Logs every kill-switch check, mandate evaluation, and risk decision to a
time-stamped JSONL file. This is forensic: when something goes wrong, you
want to know exactly what the worker saw and decided at each step.

Inspired by Vibe-Trading's audit-first design.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

AUDIT_DIR = Path(DATA_DIR) / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def _current_log_file() -> Path:
    """Return today's audit log file (one JSONL per day for easy rotation)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return AUDIT_DIR / f"audit_{today}.jsonl"


def log_event(event_type: str, details: Dict[str, Any]) -> None:
    """
    Append an audit event to today's log.

    Args:
        event_type: Category (e.g. "kill_switch_check", "mandate_decision", "risk_decision")
        details:    Arbitrary dict with event specifics (symbol, approved, reason, etc.)
    """
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            **details,
        }
        with open(_current_log_file(), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        # Audit failure is logged but never blocks trading — audit is observability,
        # not control. The kill switch and mandate gate remain authoritative.
        logger.error("Audit log write failed: %s", exc)


def log_kill_switch_check(halted: bool, reason: Optional[str] = None) -> None:
    """Log a kill switch check (called every worker tick)."""
    log_event("kill_switch_check", {"halted": halted, "reason": reason})


def log_mandate_decision(
    symbol: str,
    side: str,
    approved: bool,
    reason: Optional[str] = None,
    notional: Optional[float] = None,
) -> None:
    """Log a mandate gate decision."""
    log_event("mandate_decision", {
        "symbol": symbol,
        "side": side,
        "approved": approved,
        "reason": reason,
        "notional": notional,
    })


def log_risk_decision(
    symbol: str,
    side: str,
    quantity: float,
    approved: bool,
    reason: Optional[str] = None,
) -> None:
    """Log a risk engine decision."""
    log_event("risk_decision", {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "approved": approved,
        "reason": reason,
    })


def log_order_submission(
    symbol: str,
    side: str,
    quantity: float,
    limit_price: Optional[float],
    order_id: str,
    status: str,
) -> None:
    """Log an order submission result."""
    log_event("order_submission", {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "limit_price": limit_price,
        "order_id": order_id,
        "status": status,
    })
