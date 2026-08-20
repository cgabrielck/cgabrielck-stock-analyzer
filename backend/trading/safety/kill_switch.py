"""
Kill switch — manual emergency stop for all new BUY orders.

Design:
  - File-based sentinel: existence of data/KILL_SWITCH triggers halt
  - Worker checks on every tick before processing signals
  - Allows closing positions (SELL) even when halted
  - CLI helpers: --halt writes the file, --resume removes it

Inspired by Vibe-Trading's one-click trading freeze pattern.
"""
import os
from pathlib import Path
from typing import Optional

from backend.utils.constants import DATA_DIR

KILL_SWITCH_FILE = Path(DATA_DIR) / "KILL_SWITCH"


def is_halted() -> bool:
    """Return True if the kill switch is active (file exists)."""
    return KILL_SWITCH_FILE.exists()


def engage(reason: Optional[str] = None) -> None:
    """
    Engage the kill switch — halts all new BUY orders.
    
    Args:
        reason: Optional human-readable reason (written to the file)
    """
    KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write(reason or "Manual halt engaged\n")


def disengage() -> None:
    """Disengage the kill switch — resumes normal trading."""
    if KILL_SWITCH_FILE.exists():
        KILL_SWITCH_FILE.unlink()


def get_reason() -> Optional[str]:
    """Read the reason from the kill switch file, if present."""
    if not KILL_SWITCH_FILE.exists():
        return None
    try:
        with open(KILL_SWITCH_FILE, "r") as f:
            return f.read().strip()
    except Exception:
        return "Kill switch active (reason unknown)"
