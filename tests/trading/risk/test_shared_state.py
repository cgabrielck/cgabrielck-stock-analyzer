"""Durable Desk+worker day-start / cooldown bookkeeping (O5 extras)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.trading.risk.shared_state import (
    active_cooldown_tickers,
    load_day_start,
    mark_cooldown,
    record_day_start,
)


def test_day_start_first_write_wins():
    day, value = record_day_start(100_000.0)
    again_day, again_value = record_day_start(96_000.0)
    assert again_day == day
    assert again_value == 100_000.0
    loaded = load_day_start()
    assert loaded == (day, 100_000.0)


def test_cooldown_active_then_expires():
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    mark_cooldown("AAPL", when=now)
    assert "AAPL" in active_cooldown_tickers(now=now)
    later = now + timedelta(hours=24, seconds=1)
    assert "AAPL" not in active_cooldown_tickers(now=later)
