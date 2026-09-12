"""Tests for US-session auto Scan scheduler."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import backend.api.scan_scheduler as sched

_ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_ET)


def test_weekend_outside_scan_window():
    sat = _et(2026, 9, 12, 12, 0)  # Saturday
    assert sched.is_us_weekday(sat) is False
    assert sched.in_scan_window(sat, outside_hours=False) is False
    assert sched.due_slot(sat, interval_min=120, outside_hours=False) is None


def test_off_hours_weekday_no_slot():
    mon_night = _et(2026, 9, 14, 20, 0)  # Monday 8pm ET — after post-close
    assert sched.in_scan_window(mon_night, outside_hours=False) is False
    assert sched.due_slot(mon_night, interval_min=120, outside_hours=False) is None


def test_pre_open_slot_fires():
    pre = _et(2026, 9, 14, 9, 20).replace(second=10)
    slot = sched.due_slot(pre, interval_min=120, outside_hours=False, last_trigger_at=None)
    assert slot is not None
    assert slot.hour == 9 and slot.minute == 20


def test_same_slot_not_double_fired():
    pre = _et(2026, 9, 14, 9, 20).replace(second=5)
    first = sched.due_slot(pre, interval_min=120, outside_hours=False, last_trigger_at=None)
    assert first is not None
    again = sched.due_slot(pre, interval_min=120, outside_hours=False, last_trigger_at=first)
    assert again is None


def test_next_slot_skips_weekend():
    fri_after = _et(2026, 9, 11, 17, 0)  # Friday after post-close
    nxt = sched.next_slot_after(fri_after, 120, outside_hours=False)
    assert nxt.weekday() == 0  # Monday
    assert nxt.hour == 9 and nxt.minute == 20


def test_trigger_scan_skips_when_job_running(monkeypatch):
    existing = {"id": "abc12345-xxxx", "status": "running", "kind": "scan"}

    def fake_latest(kind):
        assert kind == "scan"
        return existing

    def boom(**kwargs):
        raise AssertionError("start_scan must not be called when a job is running")

    monkeypatch.setattr("backend.api.research_jobs.latest_job", fake_latest)
    monkeypatch.setattr("backend.api.research_jobs.start_scan", boom)

    out = sched.trigger_scan(reason="scheduler")
    assert out["already_running"] is True
    assert out["job"]["id"] == existing["id"]


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("SCAN_AUTO", raising=False)
    monkeypatch.delenv("SCAN_INTERVAL_MIN", raising=False)
    monkeypatch.delenv("SCAN_USE_LLM", raising=False)
    cfg = sched.config()
    assert cfg["enabled"] is False
    assert cfg["interval_min"] == 120
    assert cfg["use_llm"] is True
