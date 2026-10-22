"""FastAPI Desk POST /api/orders must enforce Mandate + RiskEngine (R1-SAFE)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.trading.engine.desk_orders import (
    DESK_ACTOR,
    DESK_STRATEGY_ID,
    DESK_WRITE_POINT,
    KILL_BUY_DETAIL,
    KILL_SELL_DETAIL,
    NO_CONSTRAINING_PRICE_DETAIL,
    desk_journal_has_path_stamp,
    reset_desk_mandate_gate,
)
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
        json={"symbol": "GME", "side": "buy", "quantity": 1, "limit_price": 20, "order_type": "limit"},
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
        json={"symbol": "AAPL", "side": "buy", "quantity": 10, "limit_price": 150, "order_type": "limit"},
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
    stamped = rows[-1]
    assert desk_journal_has_path_stamp(stamped)
    assert stamped["strategy_id"] == DESK_STRATEGY_ID
    assert stamped["intent_id"]
    assert stamped["links"]["intent_ref"] == ""
    assert stamped["provenance"]["write_point"] == DESK_WRITE_POINT
    assert stamped["provenance"]["actor"] == DESK_ACTOR
    assert stamped["provenance"]["verified"] is False
    assert stamped["provenance"]["live"] is False
    events = audit.read_recent_events(audit_dir=desk_api["tmp_path"] / "audit", limit=50)
    money = [
        e
        for e in events
        if e.get("event_type") in {"mandate_decision", "risk_decision", "order_submission"}
    ]
    assert money, "accepted Desk POST must write audit events"
    assert all(e.get("actor") == DESK_ACTOR for e in money)


def test_api_success_without_path_stamp_body_is_not_submitted(desk_api, monkeypatch):
    """FC-ORDER-01: missing path stamp is not 200 and must not claim submitted."""
    real = journal_store.record_from_order

    def _omit_stamp(*args, **kwargs):
        kwargs.pop("intent_id", None)
        kwargs.pop("provenance_extra", None)
        kwargs["link_intent"] = False
        return real(*args, **kwargs)

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _omit_stamp)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150, "order_type": "limit"},
    )
    assert res.status_code != 200
    assert res.status_code in {500, 409, 503, 502}
    assert '"status":"submitted"' not in res.text.replace(" ", "").lower()
    assert "submitted" not in str(res.json()).lower() or res.status_code != 200


def test_api_success_without_path_stamp_is_not_200(desk_api, monkeypatch):
    """Fail-case: FastAPI POST /api/orders must not 200 if PATH stamp is skipped."""
    real = journal_store.record_from_order

    def _omit_stamp(*args, **kwargs):
        kwargs.pop("intent_id", None)
        kwargs.pop("provenance_extra", None)
        kwargs["link_intent"] = False
        return real(*args, **kwargs)

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _omit_stamp)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150, "order_type": "limit"},
    )
    assert res.status_code != 200
    assert res.status_code == 502
    rows = journal_store.load_raw_entries()
    assert not any(desk_journal_has_path_stamp(row) for row in rows)


def test_api_success_without_actor_stamp_is_not_200(desk_api, monkeypatch):
    """O5 fail-case: FastAPI POST must not 200 if journal actor=desk is skipped."""
    real = journal_store.record_from_order

    def _omit_actor(*args, **kwargs):
        extra = dict(kwargs.get("provenance_extra") or {})
        extra.pop("actor", None)
        kwargs["provenance_extra"] = extra
        kwargs["link_intent"] = False
        return real(*args, **kwargs)

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _omit_actor)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150, "order_type": "limit"},
    )
    assert res.status_code != 200
    assert res.status_code == 502
    rows = journal_store.load_raw_entries()
    assert not any(desk_journal_has_path_stamp(row) for row in rows)


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
        json={"symbol": "GME", "side": "buy", "quantity": 1, "limit_price": 20, "order_type": "limit"},
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


def test_api_broker_502_retry_new_key_does_not_second_submit(desk_api):
    """Pre-fix main minted cgab-<uuid> per POST, so a 502 retry submitted twice."""
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        err = RuntimeError("502 Bad Gateway")
        err.status_code = 502
        raise err

    desk_api["broker"].submit_order.side_effect = _submit
    client = desk_api["client"]
    body = {"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150, "order_type": "limit"}
    first = client.post("/api/orders", json=body)
    assert first.status_code == 502, first.text
    detail = first.json()["detail"]
    assert detail["retry"] == "do_not_blind_retry"
    assert detail["acceptance"] == "unknown"
    assert detail["broker_submitted"] is None
    sticky_key = detail["idempotency_key"]
    assert sticky_key

    second = client.post("/api/orders", json=body)
    assert second.status_code == 502, second.text
    assert second.status_code != 200
    assert desk_api["broker"].submit_order.call_count == 1
    assert keys == [sticky_key]
    again = second.json()["detail"]
    assert again["idempotency_key"] == sticky_key
    assert again["retry"] == "do_not_blind_retry"
    assert "do not blind-retry" in again["message"].lower()


def test_api_journal_502_retry_new_key_does_not_second_submit(desk_api, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _boom)
    client = desk_api["client"]
    body = {"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150, "order_type": "limit"}
    first = client.post("/api/orders", json=body)
    assert first.status_code == 502, first.text
    sticky_key = first.json()["detail"]["idempotency_key"]
    second = client.post("/api/orders", json=body)
    assert second.status_code == 502, second.text
    assert second.status_code != 200
    assert desk_api["broker"].submit_order.call_count == 1
    assert second.json()["detail"]["idempotency_key"] == sticky_key
    assert second.json()["detail"]["retry"] == "do_not_blind_retry"
    assert second.json()["detail"]["broker_submitted"] is True


def test_api_journal_failure_after_accept_is_not_200(desk_api, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _boom)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150, "order_type": "limit"},
    )
    assert res.status_code != 200
    assert res.status_code == 502
    detail = res.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["journaled"] is False
    assert detail["broker_submitted"] is True
    assert "not journaled" in detail["message"].lower()
    desk_api["broker"].submit_order.assert_called_once()


def test_api_reject_journal_failure_is_not_success(desk_api, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("journal down")

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _boom)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "GME", "side": "buy", "quantity": 1, "limit_price": 20},
    )
    assert res.status_code != 200
    assert res.status_code == 500
    detail = res.json()["detail"]
    assert detail["journaled"] is False
    assert detail["broker_submitted"] is False
    assert "durable audit" in detail["message"].lower()
    desk_api["broker"].submit_order.assert_not_called()


def test_api_market_buy_tiny_limit_price_409_no_broker(desk_api, monkeypatch):
    from backend.trading.engine import desk_orders as desk_mod

    monkeypatch.setattr(desk_mod, "default_quote_price", lambda _s: 150.0)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 10000,
            "order_type": "market",
            "limit_price": 0.01,
        },
    )
    assert res.status_code == 409, res.text
    desk_api["broker"].submit_order.assert_not_called()
    assert "notional" in res.json()["detail"].lower() or "exceed" in res.json()["detail"].lower()


def test_api_market_sell_tiny_limit_price_409_no_broker(desk_api, monkeypatch):
    from backend.trading.engine import desk_orders as desk_mod

    monkeypatch.setattr(desk_mod, "default_quote_price", lambda _s: 150.0)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL",
            "side": "sell",
            "quantity": 10000,
            "order_type": "market",
            "limit_price": 0.01,
        },
    )
    assert res.status_code == 409, res.text
    desk_api["broker"].submit_order.assert_not_called()


def test_api_stop_buy_tiny_limit_price_409_no_broker(desk_api, monkeypatch):
    from backend.trading.engine import desk_orders as desk_mod

    monkeypatch.setattr(desk_mod, "default_quote_price", lambda _s: 150.0)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 10000,
            "order_type": "stop",
            "limit_price": 0.01,
        },
    )
    assert res.status_code == 409, res.text
    desk_api["broker"].submit_order.assert_not_called()
    assert "notional" in res.json()["detail"].lower() or "exceed" in res.json()["detail"].lower()


def test_api_stop_sell_tiny_limit_price_409_no_broker(desk_api, monkeypatch):
    from backend.trading.engine import desk_orders as desk_mod

    monkeypatch.setattr(desk_mod, "default_quote_price", lambda _s: 150.0)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL",
            "side": "sell",
            "quantity": 10000,
            "order_type": "stop",
            "limit_price": 0.01,
        },
    )
    assert res.status_code == 409, res.text
    desk_api["broker"].submit_order.assert_not_called()


def test_api_limit_price_zero_without_quote_409_no_broker(desk_api, monkeypatch):
    from backend.trading.engine import desk_orders as desk_mod

    monkeypatch.setattr(desk_mod, "default_quote_price", lambda _s: None)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 10,
            "order_type": "limit",
            "limit_price": 0,
        },
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == NO_CONSTRAINING_PRICE_DETAIL
    desk_api["broker"].submit_order.assert_not_called()


def test_api_market_zero_limit_without_quote_409_no_broker(desk_api, monkeypatch):
    from backend.trading.engine import desk_orders as desk_mod

    monkeypatch.setattr(desk_mod, "default_quote_price", lambda _s: None)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 10,
            "order_type": "market",
            "limit_price": 0,
        },
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == NO_CONSTRAINING_PRICE_DETAIL
    desk_api["broker"].submit_order.assert_not_called()


def test_api_limit_sell_tiny_limit_price_409_no_broker(desk_api, monkeypatch):
    from backend.trading.engine import desk_orders as desk_mod

    monkeypatch.setattr(desk_mod, "default_quote_price", lambda _s: 150.0)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL",
            "side": "sell",
            "quantity": 10000,
            "order_type": "limit",
            "limit_price": 0.01,
        },
    )
    assert res.status_code == 409, res.text
    desk_api["broker"].submit_order.assert_not_called()
    assert "notional" in res.json()["detail"].lower() or "exceed" in res.json()["detail"].lower()


def test_api_stop_limit_sell_tiny_limit_price_409_no_broker(desk_api, monkeypatch):
    from backend.trading.engine import desk_orders as desk_mod

    monkeypatch.setattr(desk_mod, "default_quote_price", lambda _s: 150.0)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL",
            "side": "sell",
            "quantity": 10000,
            "order_type": "stop_limit",
            "limit_price": 0.01,
        },
    )
    assert res.status_code == 409, res.text
    desk_api["broker"].submit_order.assert_not_called()
    assert "notional" in res.json()["detail"].lower() or "exceed" in res.json()["detail"].lower()


def test_api_limit_sell_honest_still_submits(desk_api, monkeypatch):
    from backend.trading.engine import desk_orders as desk_mod

    monkeypatch.setattr(desk_mod, "default_quote_price", lambda _s: 150.0)
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL",
            "side": "sell",
            "quantity": 1,
            "order_type": "limit",
            "limit_price": 150,
        },
    )
    assert res.status_code == 200, res.text
    desk_api["broker"].submit_order.assert_called_once()
    rows = journal_store.load_raw_entries()
    assert rows, "accepted Desk sell limit must be journaled"


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


def test_api_kill_sell_409_d_sell_kill(desk_api):
    """D-sell-kill fail-case: kill ON + Desk POST sell must not be 200/success.

    Pre-fix main (R1-SAFE buy-only kill) submitted this sell.
    """
    kill_switch.engage("api test")
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={
            "symbol": "AAPL",
            "side": "sell",
            "quantity": 1,
            "order_type": "limit",
            "limit_price": 150,
        },
    )
    assert res.status_code == 409, res.text
    assert res.status_code != 200
    body = res.json()
    assert body["detail"] == KILL_SELL_DETAIL
    assert "success" not in str(body).lower()
    desk_api["broker"].get_account_summary.assert_not_called()
    desk_api["broker"].submit_order.assert_not_called()
    assert journal_store.load_raw_entries(), "rejected kill-sell must still journal"


def test_api_kill_market_sell_409_d_sell_kill(desk_api):
    """Same fail-case for market sell — kill must not be buy-only."""
    kill_switch.engage("api test")
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "sell", "quantity": 1, "order_type": "market"},
    )
    assert res.status_code == 409, res.text
    assert res.status_code != 200
    assert res.json()["detail"] == KILL_SELL_DETAIL
    desk_api["broker"].submit_order.assert_not_called()


def test_kill_on_blocks_desk_market_sell_and_journals(desk_api):
    """FC-KILL-01: kill ON + market sell is 409, broker call_count==0, journal reject."""
    kill_switch.engage("failcase kill")
    client = desk_api["client"]
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "sell", "quantity": 1, "order_type": "market"},
    )
    assert res.status_code == 409, res.text
    assert desk_api["broker"].submit_order.call_count == 0
    rows = journal_store.load_raw_entries()
    assert rows, "rejected kill-sell must still journal"
    blob = json.dumps(rows, default=str).lower()
    assert "kill" in blob or "reject" in blob or "blocked" in blob


def test_desk_corrupt_mandate_rejects_buy_and_ops_not_loaded(desk_api):
    """FC-MANDATE-01: corrupt mandate.json fail-closes Desk buy; ops not loaded."""
    desk_api["mandate_path"].write_text("{{{not json", encoding="utf-8")
    reset_desk_mandate_gate()
    client = desk_api["client"]
    ops = client.get("/api/ops").json()
    assert ops["mandate_status"] in {"missing", "corrupt", "error"}
    assert ops["mandate"] is None
    res = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150, "order_type": "limit"},
    )
    assert res.status_code != 200
    desk_api["broker"].submit_order.assert_not_called()


def test_api_daily_limit_survives_gate_reset(desk_api):
    """R4 fail-case: FastAPI Desk POST shares the durable counter across gate reset.

    Pre-fix main: reset_desk_mandate_gate() let a second buy through the same day.
    """
    desk_api["mandate_path"].write_text(
        json.dumps({**TIGHT, "max_daily_orders": 1}), encoding="utf-8"
    )
    client = desk_api["client"]
    first = client.post(
        "/api/orders",
        json={"symbol": "AAPL", "side": "buy", "quantity": 1, "limit_price": 150, "order_type": "limit"},
    )
    assert first.status_code == 200, first.text
    reset_desk_mandate_gate()
    second = client.post(
        "/api/orders",
        json={"symbol": "MSFT", "side": "buy", "quantity": 1, "limit_price": 150, "order_type": "limit"},
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert "daily order limit" in str(detail).lower()
    desk_api["broker"].submit_order.assert_called_once()
    rows = journal_store.load_raw_entries()
    assert any("daily order limit" in str(row).lower() for row in rows)
