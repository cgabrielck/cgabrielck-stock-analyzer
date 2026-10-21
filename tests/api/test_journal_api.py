from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.trading.models import Order, OrderSide, OrderStatus, OrderType
from backend.trading.storage import JSONOrderStore
from backend.utils.provenance import journal_store


@pytest.fixture
def journal_env(tmp_path, monkeypatch):
    journal = tmp_path / "trade_journal.json"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    orders = tmp_path / "orders.json"
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    from backend.trading.safety import audit

    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr("backend.trading.telegram_inbox.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.scan_scheduler.start_background", lambda: (lambda: None))
    store = JSONOrderStore(filepath=str(orders))
    monkeypatch.setenv("APCA_PAPER", "true")
    return {"journal": journal, "audit_dir": audit_dir, "store": store}


@pytest.fixture
def client(journal_env, monkeypatch):
    from backend.api import app as app_mod

    monkeypatch.setattr(app_mod, "_store", journal_env["store"])
    with TestClient(app_mod.app, raise_server_exceptions=False) as c:
        yield c


def test_get_journal_empty_does_not_invent_fills(client):
    res = client.get("/api/journal")
    assert res.status_code == 200
    body = res.json()
    assert body["schema_version"] == "trade_log.v0"
    assert body["entries"] == []
    assert body["empty"] is True
    assert body["empty_reason"] == "no_recorded_events"
    assert body["verified"] is False
    assert body["live"] is False
    assert "not a live" in body["disclaimer"].lower() or "Empty" in body["disclaimer"]


def test_post_note_is_structured_trade_log(client):
    res = client.post("/api/journal", json={"symbol": "aapl", "note": "Watching setup"})
    assert res.status_code == 200
    body = res.json()
    assert body["empty"] is False
    entry = body["entries"][0]
    assert entry["schema_version"] == "trade_log.v0"
    assert entry["symbol"] == "AAPL"
    assert entry["status"] == "proposed"
    assert entry["kind"] == "note"
    assert entry["fills"] == []
    assert entry["user_visible_summary"] == "Watching setup"
    assert entry["provenance"]["verified"] is False
    assert entry["provenance"]["source"] == "trade_journal"


def test_risk_reject_from_order_store_appears(client, journal_env):
    journal_env["store"].save_order(Order(
        id="rej-1",
        symbol="NVDA",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        limit_price=120.0,
        status=OrderStatus.REJECTED,
        error_message="Risk Gate Rejected: Insufficient buying power. Need $1200, have $50.",
        idempotency_key="idem-rej-1",
    ))
    res = client.get("/api/journal")
    assert res.status_code == 200
    rows = res.json()["entries"]
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "NVDA"
    assert row["status"] == "risk_rejected"
    assert "risk_rejected" in row["reason_codes"]
    assert "buying_power" in row["reason_codes"]
    assert row["fills"] == []
    assert row["provenance"]["source"] == "order_store"
    assert row["provenance"]["verified"] is False


def test_audit_reject_is_followable(client, journal_env):
    from backend.trading.safety import audit

    event_id = audit.log_risk_decision(
        "TSLA", "buy", 5.0, False, "Position concentration 30.0% exceeds 25.0% limit."
    )
    assert event_id
    listed = client.get("/api/journal")
    assert listed.status_code == 200
    row = next(e for e in listed.json()["entries"] if e["symbol"] == "TSLA")
    assert row["status"] == "risk_rejected"
    assert "concentration" in row["reason_codes"]
    assert row["links"]["audit_ref"] == f"/api/journal/audit/{event_id}"

    detail = client.get(row["links"]["audit_ref"])
    assert detail.status_code == 200
    body = detail.json()
    assert body["event_id"] == event_id
    assert body["kind"] == "risk_reject"
    assert body["schema_version"] == "audit.v0"

    entry = client.get(f"/api/journal/entry/{row['log_id']}")
    assert entry.status_code == 200
    assert entry.json()["entry"]["log_id"] == row["log_id"]
    assert entry.json()["audit"]["event_id"] == event_id


def test_submitted_order_journal_keeps_intent_link(client, journal_env):
    from backend.trading.models import Order, OrderSide, OrderStatus, OrderType
    from backend.utils.provenance.journal_store import record_from_order

    order = Order(
        id="ord-ok",
        symbol="MSFT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=4,
        limit_price=300.0,
        status=OrderStatus.SUBMITTED,
        idempotency_key="idem-ok",
    )
    record_from_order(order, intent_id="int-ok", strategy_id="stable")
    res = client.get("/api/journal")
    assert res.status_code == 200
    row = next(e for e in res.json()["entries"] if e["order_id"] == "ord-ok")
    assert row["intent_id"] == "int-ok"
    assert row["links"]["intent_ref"] == "/api/intents/int-ok"
    assert row["status"] == "submitted"
    assert row["fills"] == []
    assert row["provenance"]["verified"] is False
    assert row["provenance"]["live"] is False


def test_missing_audit_is_404(client):
    res = client.get("/api/journal/audit/does-not-exist")
    assert res.status_code == 404


def test_desk_html_has_journal_columns():
    html = Path("web/static/index.html").read_text(encoding="utf-8")
    assert "journal.col.status" in html
    assert "journal.col.reasons" in html
    assert "journal.col.provenance" in html
    assert "交易日誌" in html
    assert "/api/journal/audit/" in html or "journal/audit" in html
