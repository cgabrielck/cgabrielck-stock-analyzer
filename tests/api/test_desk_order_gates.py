"""FastAPI Desk POST /api/orders must enforce Mandate + RiskEngine (R1-SAFE)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.trading.engine.desk_orders import KILL_BUY_DETAIL, reset_desk_mandate_gate
from backend.trading.engine.order_manager import InMemoryOrderStore
from backend.trading.models import AccountSummary, OrderStatus
from backend.trading.safety import audit, kill_switch
from backend.utils.provenance import journal_store


TIGHT = {
    "allowed_symbols": ["AAPL", "MSFT"],
    "blocked_symbols": ["GME"],
    "max_notional_per_order": 10_000.0,
    "max_daily_orders": 50,
    "allowed_sides": ["buy", "sell"],
}


@pytest.fixture
def desk_api(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    journal = tmp_path / "trade_journal.json"
    mandate_path = tmp_path / "mandate.json"
    mandate_path.write_text(json.dumps(TIGHT), encoding="utf-8")
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH")
    monkeypatch.setattr("backend.trading.safety.mandate.REPO_MANDATE_FILE", mandate_path)
    monkeypatch.setattr("backend.trading.safety.mandate.BACKEND_MANDATE_FILE", mandate_path)
    monkeypatch.setattr("backend.trading.telegram_inbox.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.scan_scheduler.start_background", lambda: (lambda: None))
    monkeypatch.setenv("APCA_PAPER", "true")
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "unit-test-operator")
    reset_desk_mandate_gate()

    broker = MagicMock()
    broker.get_account_summary.return_value = AccountSummary(
        cash=50_000.0,
        buying_power=50_000.0,
        portfolio_value=100_000.0,
        positions=[],
    )

    def _submit(order):
        order.status = OrderStatus.SUBMITTED
        order.id = "broker-api-1"
        return order

    broker.submit_order.side_effect = _submit
    store = InMemoryOrderStore()

    from backend.api import app as app_mod

    monkeypatch.setattr(app_mod, "_broker", broker)
    monkeypatch.setattr(app_mod, "_store", store)

    with TestClient(app_mod.app, raise_server_exceptions=False) as client:
        client.headers.update({"X-Desk-Operator-Token": "unit-test-operator"})
        yield {
            "client": client,
            "broker": broker,
            "store": store,
            "mandate_path": mandate_path,
            "journal": journal,
            "tmp_path": tmp_path,
        }
    reset_desk_mandate_gate()


def test_api_mandate_reject_gme(desk_api):
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "GME", "side": "buy", "quantity": 1, "limit_price": 20},
    )
    assert res.status_code == 409, res.text
    assert "block" in res.json()["detail"].lower()
    desk_api["broker"].submit_order.assert_not_called()


def test_api_risk_reject_notional(desk_api, monkeypatch):
    from backend.trading.risk.gates import RiskEngine, RiskLimits
    from backend.trading.engine import desk_orders as desk_mod

    monkeypatch.setattr(desk_mod, "_default_risk_engine", RiskEngine(RiskLimits(max_order_notional=500.0)))
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "buy", "quantity": 10, "limit_price": 150},
    )
    assert res.status_code == 409, res.text
    assert "Risk Gate Rejected" in res.json()["detail"]
    desk_api["broker"].submit_order.assert_not_called()


def test_api_happy_path_still_submits(desk_api):
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150, "order_type": "limit"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["symbol"] == "AAPL"
    assert body["status"] in ("submitted", "accepted", "filled")
    desk_api["broker"].submit_order.assert_called_once()
    rows = journal_store.load_raw_entries()
    assert rows, "accepted Desk order must be journaled"


def test_api_ops_mandate_matches_order_path(desk_api):
    client = desk_api["client"]
    ops = client.get("/api/ops")
    assert ops.status_code == 200
    body = ops.json()
    assert body["mandate_status"] == "loaded"
    assert body["mandate_path"] == str(desk_api["mandate_path"])
    assert "GME" in body["mandate"]["blocked_symbols"]
    denied = client.post(
        "/api/orders",
        json={"symbol": "GME", "side": "buy", "quantity": 1, "limit_price": 20},
    )
    assert denied.status_code == 409
    # Same source: blocked list shown on ops is what rejected the order.
    assert "GME" in denied.json()["detail"] or "block" in denied.json()["detail"].lower()


def test_api_missing_mandate_fail_closed_and_ops_honest(desk_api, tmp_path, monkeypatch):
    missing = tmp_path / "gone.json"
    monkeypatch.setattr("backend.trading.safety.mandate.REPO_MANDATE_FILE", missing)
    monkeypatch.setattr("backend.trading.safety.mandate.BACKEND_MANDATE_FILE", missing)
    client = desk_api["client"]
    ops = client.get("/api/ops").json()
    assert ops["mandate_status"] == "missing"
    assert ops["mandate"] is None
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150},
    )
    assert res.status_code == 409
    assert "fail closed" in res.json()["detail"].lower()
    desk_api["broker"].submit_order.assert_not_called()


def test_api_kill_buy_409(desk_api):
    kill_switch.engage("api test")
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150},
    )
    assert res.status_code == 409
    assert res.json()["detail"] == KILL_BUY_DETAIL
    desk_api["broker"].submit_order.assert_not_called()
