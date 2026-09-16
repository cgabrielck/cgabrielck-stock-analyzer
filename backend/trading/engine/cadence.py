"""Operator-chosen paper cadence. Default is the current 24h demo (intraday).

``weekly`` = US Monday rebalance + PANIC (high_volatility) exception.
This switch never rewrites IGNORE_MARKET_HOURS.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

CADENCE_INTRADAY = "intraday"
CADENCE_WEEKLY = "weekly"
KNOWN_CADENCES = (CADENCE_INTRADAY, CADENCE_WEEKLY)

PANIC_REGIMES = frozenset({"high_volatility", "panic"})
_NY = ZoneInfo("America/New_York")


def normalize_cadence(raw: Any) -> str:
    """Unknown / empty values fall back to intraday — never silently weekly."""
    text = str(raw or "").strip().lower()
    if text in ("weekly", "week", "monday", "weekly_monday", "monday_rebalance"):
        return CADENCE_WEEKLY
    return CADENCE_INTRADAY


def cadence_from_env() -> Optional[str]:
    raw = (os.getenv("WORKER_CADENCE") or os.getenv("PAPER_CADENCE") or "").strip()
    if not raw:
        return None
    return normalize_cadence(raw)


def resolve_cadence(ai_mode: Optional[Mapping[str, Any]] = None) -> str:
    """Env wins when set; else ai_mode.json; else current 24h demo."""
    env = cadence_from_env()
    if env is not None:
        return env
    if ai_mode:
        return normalize_cadence(ai_mode.get("cadence"))
    return CADENCE_INTRADAY


def is_us_monday(now_utc: Optional[datetime] = None) -> bool:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(_NY).weekday() == 0


def regime_is_panic(regime: Optional[Mapping[str, Any]]) -> bool:
    name = str((regime or {}).get("regime") or "").strip().lower()
    return name in PANIC_REGIMES


def allows_new_entries(
    cadence: str,
    now_utc: Optional[datetime] = None,
    regime: Optional[Mapping[str, Any]] = None,
) -> bool:
    if normalize_cadence(cadence) != CADENCE_WEEKLY:
        return True
    if regime_is_panic(regime):
        return True
    return is_us_monday(now_utc)


def load_ai_mode_cadence(payload: Optional[Dict[str, Any]] = None) -> str:
    return resolve_cadence(payload)
