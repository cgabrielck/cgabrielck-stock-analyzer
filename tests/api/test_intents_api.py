"""Desk IntentStore API — local/unverified, operator-only, no live claim."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.api.desk_auth import GUEST_FORBIDDEN
from backend.trading.intents.models import IntentStatus
from backend.trading.intents.store import JSONIntentStore

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)
EXPIRY = NOW + timedelta(hours=1)


def _body(**overrides):
    data = {
        "schema_version": "intent.v0",
        "symbol": "AAPL",
        "side": "buy",
        "stop": {"type": "pct", "value": 0.10},
        "conviction_tier": "med",
        "expiry": EXPIRY.isoformat(),
        "as_of": NOW.isoformat(),
        "strategy_id": "stable",
    }
    data.update(overrides)
    return data


@pytest.fixture
def intent_env(tmp_path, monkeypatch):
    store = JSONIntentStore(filepath=str(tmp_path / "intents.json"))
    monkeypatch.setattr("backend.api.intents._store", store)
    monkeypatch.setattr("backend.trading.telegram_inbox.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.scan_scheduler.start_background", lambda: (lambda: None))
    monkeypatch.setenv("APCA_PAPER", "true")
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "unit-test-operator")
    return store


@pytest.fixture
def operator_client(intent_env):
    from backend.api.app import app

    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers.update({"X-Desk-Operator-Token": "unit-test-operator"})
        yield client


@pytest.fixture
def guest_client(intent_env):
    from backend.api.app import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_write_list_get(operator_client):
    posted = operator_client.post("/api/intents", json=_body())
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["verified"] is False
    assert body["live"] is False
    assert body["local"] is True
    assert body["schema_version"] == "intent.v0"
    intent = body["intent"]
    assert intent["symbol"] == "AAPL"
    assert "quantity" not in intent
    intent_id = intent["intent_id"]

    listed = operator_client.get("/api/intents")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["verified"] is False
    assert payload["live"] is False
    assert payload["count"] == 1
    assert payload["intents"][0]["intent_id"] == intent_id

    got = operator_client.get(f"/api/intents/{intent_id}")
    assert got.status_code == 200
    assert got.json()["intent"]["status"] == "active"


def test_forbidden_qty_rejected(operator_client):
    res = operator_client.post("/api/intents", json=_body(quantity=10))
    assert res.status_code == 422


def test_buy_without_stop_rejected(operator_client):
    payload = _body()
    payload["stop"] = None
    res = operator_client.post("/api/intents", json=payload)
    assert res.status_code == 422


def test_post_same_id_after_consumed_is_409(operator_client, intent_env):
    posted = operator_client.post("/api/intents", json=_body(intent_id="once"))
    assert posted.status_code == 200, posted.text
    intent_env.set_status("once", IntentStatus.CONSUMED)
    again = operator_client.post("/api/intents", json=_body(intent_id="once"))
    assert again.status_code == 409
    assert intent_env.get("once").status is IntentStatus.CONSUMED


def test_cancel_after_consumed_is_409(operator_client, intent_env):
    intent_id = operator_client.post("/api/intents", json=_body()).json()["intent"]["intent_id"]
    intent_env.set_status(intent_id, IntentStatus.CONSUMED)
    res = operator_client.post(f"/api/intents/{intent_id}/cancel")
    assert res.status_code == 409
    assert "consumed" in str(res.json().get("detail", "")).lower()
    assert intent_env.get(intent_id).status is IntentStatus.CONSUMED


def test_cancel_status_transition(operator_client):
    intent_id = operator_client.post("/api/intents", json=_body()).json()["intent"]["intent_id"]
    cancelled = operator_client.post(f"/api/intents/{intent_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["intent"]["status"] == "cancelled"
    again = operator_client.post(f"/api/intents/{intent_id}/cancel")
    assert again.status_code == 409


def test_missing_intent_404(operator_client):
    res = operator_client.get("/api/intents/does-not-exist")
    assert res.status_code == 404


def test_guest_forbidden(guest_client):
    res = guest_client.get("/api/intents")
    assert res.status_code == 403
    assert GUEST_FORBIDDEN in str(res.json().get("detail"))
    posted = guest_client.post("/api/intents", json=_body())
    assert posted.status_code == 403


def test_from_order_links_intent_id():
    from backend.trading.models import Order, OrderSide, OrderType
    from backend.utils.provenance.trade_log import from_order

    order = Order(
        id="ord-link",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=4,
        limit_price=150.0,
        idempotency_key="idem-link",
    )
    entry = from_order(order, intent_id="int-link")
    assert entry["intent_id"] == "int-link"
    assert entry["links"]["intent_ref"] == "/api/intents/int-link"
    assert entry["provenance"]["verified"] is False
    assert entry["provenance"]["live"] is False
