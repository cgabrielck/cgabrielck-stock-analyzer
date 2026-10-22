"""Tests for kill_switch.py — emergency trading halt (module-function API)."""
import json

import pytest

from backend.trading.safety import audit, kill_switch


@pytest.fixture(autouse=True)
def isolated_switch(tmp_path, monkeypatch):
    """Redirect the kill-switch sentinel file into a temp dir per test."""
    fake_file = tmp_path / "KILL_SWITCH"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FILE", fake_file)
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)
    yield {"file": fake_file, "audit_dir": audit_dir}


def test_initial_state_disengaged():
    """By default, the kill switch is off."""
    assert not kill_switch.is_halted()
    assert kill_switch.get_reason() is None


def test_engage_and_disengage():
    """Engage with a reason, then disengage."""
    kill_switch.engage("Market crash detected")
    assert kill_switch.is_halted()
    assert kill_switch.get_reason() == "Market crash detected"

    kill_switch.disengage()
    assert not kill_switch.is_halted()
    assert kill_switch.get_reason() is None


def test_persistence_via_file(isolated_switch):
    """State is written to disk (the sentinel file exists when engaged)."""
    kill_switch.engage("API outage")
    assert isolated_switch["file"].exists()
    assert kill_switch.is_halted()


def test_engage_default_reason():
    """Engage with no reason writes a default message."""
    kill_switch.engage()
    assert kill_switch.is_halted()
    assert "Manual halt" in (kill_switch.get_reason() or "")


def test_re_engage_overwrites_reason():
    """Re-engaging updates the reason."""
    kill_switch.engage("first reason")
    kill_switch.engage("second reason")
    assert kill_switch.get_reason() == "second reason"


def test_disengage_when_already_off():
    """Idempotent — disengage when off is a no-op."""
    kill_switch.disengage()
    assert not kill_switch.is_halted()


def test_engage_creates_parent_dir(tmp_path, monkeypatch):
    """engage() creates the parent directory if missing."""
    nested = tmp_path / "deep" / "nested" / "KILL_SWITCH"
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FILE", nested)
    kill_switch.engage("test")
    assert nested.exists()
    assert kill_switch.is_halted()


def _audit_rows(isolated_switch):
    files = list(isolated_switch["audit_dir"].glob("audit_*.jsonl"))
    rows = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_engage_writes_audit_who_what_when_evidence(isolated_switch):
    """D3 fail-case: engage must not look like nothing happened in the audit log."""
    kill_switch.engage(
        "Market crash detected",
        actor="desk_ops",
        source="POST /api/ops/kill",
    )
    rows = _audit_rows(isolated_switch)
    assert rows, "kill engage must write an audit event"
    row = rows[-1]
    assert row["event_type"] == "kill_switch_engage"
    assert row["kind"] == "kill"
    assert row["actor"] == "desk_ops"
    assert row["reason"] == "Market crash detected"
    assert row["source"] == "POST /api/ops/kill"
    assert row["halted"] is True
    assert row["engaged"] is True
    assert row["sentinel"] == str(isolated_switch["file"])
    assert row["ts"]
    assert row["event_id"]
    assert row.get("verified") is not True
    assert row.get("live") is not True


def test_resume_clears_writes_audit_event(isolated_switch):
    """D3 fail-case: resume that actually clears must appear in the audit log."""
    kill_switch.engage("halt", actor="desk_ops", source="POST /api/ops/kill")
    kill_switch.disengage(actor="desk_ops", source="POST /api/ops/resume")
    rows = _audit_rows(isolated_switch)
    kinds = [row["kind"] for row in rows]
    assert "kill" in kinds
    assert "resume" in kinds
    resume = next(row for row in rows if row["kind"] == "resume")
    assert resume["event_type"] == "kill_switch_resume"
    assert resume["actor"] == "desk_ops"
    assert resume["halted"] is False
    assert resume["source"] == "POST /api/ops/resume"
    assert resume["reason"]
    assert resume["ts"]


def test_resume_when_already_off_does_not_invent_cleared_event(isolated_switch):
    kill_switch.disengage(actor="desk_ops", source="POST /api/ops/resume")
    assert _audit_rows(isolated_switch) == []
