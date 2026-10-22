"""
Kill switch — manual emergency stop for all new BUY orders.

Design:
  - File-based sentinel: existence of data/KILL_SWITCH triggers halt
  - Worker checks on every tick before processing signals
  - Allows closing positions (SELL) even when halted — worker flatten /
    exit-while-halted goes through Mandate + Risk + OrderManager
  - Desk ``place_desk_order`` / POST sell is independently 409 (D-sell-kill);
    do not import ``worker_kill_blocks_side`` on the Desk path
  - CLI helpers: --halt writes the file, --resume removes it
  - Engage/resume always write an audit event (D3) so the journal is not empty

Inspired by Vibe-Trading's one-click trading freeze pattern.
"""
from pathlib import Path
from typing import Optional

from backend.utils.constants import DATA_DIR

KILL_SWITCH_FILE = Path(DATA_DIR) / "KILL_SWITCH"


def is_halted() -> bool:
    """Return True if the kill switch is active (file exists)."""
    return KILL_SWITCH_FILE.exists()


def worker_kill_blocks_side(side: object) -> bool:
    """Kill ON blocks worker BUY entries only.

    Flatten/exit SELL stays allowed so open risk can still close through
    Mandate + Risk + OrderManager. Desk ``place_desk_order`` must not call
    this helper — Desk sell remains HTTP 409 (D-sell-kill).
    """
    if not is_halted():
        return False
    if hasattr(side, "value"):
        key = str(getattr(side, "value"))
    else:
        key = str(side)
    return key.strip().lower() == "buy"


def engage(
    reason: Optional[str] = None,
    *,
    actor: str = "unknown",
    source: Optional[str] = None,
) -> None:
    """
    Engage the kill switch — halts all new BUY orders.

    Args:
        reason: Optional human-readable reason (written to the file)
        actor: Who engaged the halt (desk_ops / telegram / cli / …)
        source: Evidence of the control-plane action (endpoint, command, …)
    """
    previous = is_halted()
    text = reason or "Manual halt engaged\n"
    KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write(text)
    _audit_engage(
        actor=actor,
        source=source,
        reason=(text.strip() or "Manual halt engaged"),
        previous_halted=previous,
    )


def disengage(*, actor: str = "unknown", source: Optional[str] = None) -> None:
    """Disengage the kill switch — resumes normal trading."""
    previous = is_halted()
    if KILL_SWITCH_FILE.exists():
        KILL_SWITCH_FILE.unlink()
    if not previous:
        # Resume when already off is a no-op — do not invent a cleared event.
        return
    _audit_resume(actor=actor, source=source)


def get_reason() -> Optional[str]:
    """Read the reason from the kill switch file, if present."""
    if not KILL_SWITCH_FILE.exists():
        return None
    try:
        with open(KILL_SWITCH_FILE, "r") as f:
            return f.read().strip()
    except Exception:
        return "Kill switch active (reason unknown)"


def _audit_engage(
    *,
    actor: str,
    source: Optional[str],
    reason: str,
    previous_halted: bool,
) -> None:
    """Best-effort forensic row. Sentinel file remains the control; audit never blocks."""
    try:
        from backend.trading.safety import audit

        audit.log_kill_switch_engage(
            reason,
            actor=actor or "unknown",
            source=source or "kill_switch_engage",
            previous_halted=previous_halted,
            sentinel=str(KILL_SWITCH_FILE),
        )
    except Exception:
        return


def _audit_resume(*, actor: str, source: Optional[str]) -> None:
    try:
        from backend.trading.safety import audit

        audit.log_kill_switch_resume(
            "Kill switch cleared",
            actor=actor or "unknown",
            source=source or "kill_switch_resume",
            sentinel=str(KILL_SWITCH_FILE),
        )
    except Exception:
        return
