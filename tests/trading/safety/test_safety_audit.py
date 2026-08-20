"""Tests for audit.py — append-only decision log."""
import json
from pathlib import Path
import pytest

from backend.trading.safety import audit


@pytest.fixture(autouse=True)
def isolated_audit_dir(tmp_path, monkeypatch):
    """Redirect audit logs into a temp directory per test."""
    fake_dir = tmp_path / "audit"
    fake_dir.mkdir()
    monkeypatch.setattr(audit, "AUDIT_DIR", fake_dir)
    return fake_dir


def test_log_event_creates_jsonl_entry(isolated_audit_dir):
    """log_event() writes a timestamped JSON line."""
    audit.log_event("test_event", {"key": "value", "count": 42})
    
    log_files = list(isolated_audit_dir.glob("audit_*.jsonl"))
    assert len(log_files) == 1
    
    with open(log_files[0], "r") as f:
        lines = f.readlines()
    assert len(lines) == 1
    
    entry = json.loads(lines[0])
    assert entry["event_type"] == "test_event"
    assert entry["key"] == "value"
    assert entry["count"] == 42
    assert "ts" in entry


def test_log_kill_switch_check(isolated_audit_dir):
    """log_kill_switch_check() writes the halted state."""
    audit.log_kill_switch_check(halted=True, reason="Manual halt")
    
    log_file = list(isolated_audit_dir.glob("*.jsonl"))[0]
    with open(log_file, "r") as f:
        entry = json.loads(f.read())
    
    assert entry["event_type"] == "kill_switch_check"
    assert entry["halted"] is True
    assert entry["reason"] == "Manual halt"


def test_log_mandate_decision(isolated_audit_dir):
    """log_mandate_decision() records authorization outcomes."""
    audit.log_mandate_decision(
        symbol="AAPL",
        side="buy",
        approved=False,
        reason="blocked_symbol",
        notional=10_000.0,
    )
    
    log_file = list(isolated_audit_dir.glob("*.jsonl"))[0]
    with open(log_file, "r") as f:
        entry = json.loads(f.read())
    
    assert entry["event_type"] == "mandate_decision"
    assert entry["symbol"] == "AAPL"
    assert entry["approved"] is False
    assert entry["notional"] == 10_000.0


def test_log_risk_decision(isolated_audit_dir):
    """log_risk_decision() records RiskEngine outcomes."""
    audit.log_risk_decision(
        symbol="TSLA",
        side="buy",
        quantity=50.0,
        approved=True,
        reason="within_limits",
    )
    
    log_file = list(isolated_audit_dir.glob("*.jsonl"))[0]
    with open(log_file, "r") as f:
        entry = json.loads(f.read())
    
    assert entry["event_type"] == "risk_decision"
    assert entry["symbol"] == "TSLA"
    assert entry["quantity"] == 50.0


def test_log_order_submission(isolated_audit_dir):
    """log_order_submission() records final order results."""
    audit.log_order_submission(
        symbol="NVDA",
        side="buy",
        quantity=10.0,
        limit_price=850.0,
        order_id="ord-123",
        status="submitted",
    )
    
    log_file = list(isolated_audit_dir.glob("*.jsonl"))[0]
    with open(log_file, "r") as f:
        entry = json.loads(f.read())
    
    assert entry["event_type"] == "order_submission"
    assert entry["order_id"] == "ord-123"


def test_multiple_events_append(isolated_audit_dir):
    """Multiple events append to the same file (same day)."""
    audit.log_event("event_a", {"id": 1})
    audit.log_event("event_b", {"id": 2})
    audit.log_event("event_c", {"id": 3})
    
    log_file = list(isolated_audit_dir.glob("*.jsonl"))[0]
    with open(log_file, "r") as f:
        lines = f.readlines()
    
    assert len(lines) == 3
    ids = [json.loads(line)["id"] for line in lines]
    assert ids == [1, 2, 3]


def test_audit_failure_does_not_raise(isolated_audit_dir, monkeypatch):
    """Audit failures are logged but never propagate exceptions."""
    # Simulate write failure by making the dir read-only
    isolated_audit_dir.chmod(0o444)
    
    # Should log an error but not raise
    audit.log_event("should_fail", {"data": "test"})
    
    # Restore permissions for cleanup
    isolated_audit_dir.chmod(0o755)
