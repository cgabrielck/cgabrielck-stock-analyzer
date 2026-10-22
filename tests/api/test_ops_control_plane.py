"""D3: ops kill/resume must leave an honest journal/audit trail."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.trading.safety import audit, kill_switch
from backend.utils.provenance import journal_store


@pytest.fixture
def ops_env(tmp_path, monkeypatch):
    journal = tmp_path / "trade_journal.json"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    kill_file = tmp_path / "KILL_SWITCH"
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FILE", kill_file)
    monkeypatch.setattr("backend.trading.telegram_inbox.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.scan_scheduler.start_background", lambda: (lambda: None))
    monkeypatch.setenv("APCA_PAPER", "true")
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "unit-test-operator")
    return {"journal": journal, "audit_dir": audit_dir, "kill_file": kill_file}


@pytest.fixture
def client(ops_env):
    from backend.api import app as app_mod

    with TestClient(app_mod.app, raise_server_exceptions=False) as c:
        c.headers.update({"X-Desk-Operator-Token": "unit-test-operator"})
        yield c


def _journal_rows(client):
    res = client.get("/api/journal")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("verified") is not True
    assert body.get("live") is not True
    return body


def test_ops_kill_appears_in_journal(client, ops_env):
    """D3 fail-case: POST /api/ops/kill must not look like nothing happened."""
    before = _journal_rows(client)
    assert before["entries"] == []
    assert before["empty"] is True

    res = client.post("/api/ops/kill", params={"reason": "Manual halt from Cgab"})
    assert res.status_code == 200, res.text
    ops = res.json()
    assert ops["kill_switch"] is True
    assert ops_env["kill_file"].exists()

    listed = _journal_rows(client)
    assert listed["empty"] is False
    kill_rows = [
        row
        for row in listed["entries"]
        if "kill_switch" in (row.get("reason_codes") or [])
        or "halt" in str(row.get("user_visible_summary") or "").lower()
    ]
    assert kill_rows, "ops kill must surface in GET /api/journal"
    row = kill_rows[0]
    assert row["user_visible_summary"]
    assert row["provenance"]["verified"] is False
    assert row["provenance"]["live"] is False
    assert row["links"]["audit_ref"]
    detail = client.get(row["links"]["audit_ref"])
    assert detail.status_code == 200, detail.text
    event = detail.json()
    assert event["kind"] == "kill"
    assert event["event_type"] == "kill_switch_engage"
    assert event["actor"] == "desk_ops"
    assert event["reason"] == "Manual halt from Cgab"
    assert event["source"] == "POST /api/ops/kill"
    assert event["ts"]
    assert event.get("verified") is not True
    assert event.get("live") is not True


def test_ops_resume_appears_in_journal(client, ops_env):
    """D3 fail-case: resume that clears kill must appear; not an empty trace."""
    halt = client.post("/api/ops/kill", params={"reason": "ops halt"})
    assert halt.status_code == 200
    res = client.post("/api/ops/resume")
    assert res.status_code == 200, res.text
    assert res.json()["kill_switch"] is False
    assert not ops_env["kill_file"].exists()

    listed = _journal_rows(client)
    summaries = [row.get("user_visible_summary") for row in listed["entries"]]
    assert any(s and "cleared" in str(s).lower() for s in summaries)
    resume_rows = [
        row for row in listed["entries"] if row.get("kind") == "note" and "cleared" in str(row.get("user_visible_summary") or "").lower()
    ]
    assert resume_rows
    ref = resume_rows[0]["links"]["audit_ref"]
    event = client.get(ref).json()
    assert event["kind"] == "resume"
    assert event["event_type"] == "kill_switch_resume"
    assert event["actor"] == "desk_ops"
    assert event["source"] == "POST /api/ops/resume"
    assert event["halted"] is False
