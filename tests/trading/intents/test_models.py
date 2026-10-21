"""Intent v0 validation — required fields, buy stop, forbidden L2 fields."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.trading.intents.models import (
    INTENT_SCHEMA_VERSION,
    IntentStatus,
    IntentValidationError,
    parse_intent,
)

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
EXPIRY = NOW + timedelta(hours=1)


def valid_payload(**overrides):
    data = {
        "schema_version": INTENT_SCHEMA_VERSION,
        "intent_id": "int-1",
        "symbol": "aapl",
        "side": "buy",
        "stop": {"type": "pct", "value": 0.10},
        "conviction_tier": "med",
        "expiry": EXPIRY.isoformat(),
        "as_of": NOW.isoformat(),
        "status": "active",
    }
    data.update(overrides)
    return data


def test_parse_valid_buy_intent():
    intent = parse_intent(valid_payload())
    assert intent.schema_version == INTENT_SCHEMA_VERSION
    assert intent.symbol == "AAPL"
    assert intent.side.value == "buy"
    assert intent.stop is not None
    assert intent.stop.type == "pct"
    assert intent.stop.value == 0.10
    assert intent.conviction_tier == "med"
    assert intent.status is IntentStatus.ACTIVE
    dumped = intent.model_dump_json_dict()
    for key in ("quantity", "qty", "shares", "notional", "limit_price", "order_type"):
        assert key not in dumped


def test_sell_flatten_allows_null_stop():
    intent = parse_intent(valid_payload(side="sell", stop=None))
    assert intent.side.value == "sell"
    assert intent.stop is None


def test_buy_requires_stop():
    with pytest.raises(IntentValidationError, match="stop is required"):
        parse_intent(valid_payload(stop=None))


def test_buy_stop_type_and_value():
    with pytest.raises(IntentValidationError):
        parse_intent(valid_payload(stop={"type": "atr", "value": 0.10}))
    with pytest.raises(IntentValidationError):
        parse_intent(valid_payload(stop={"type": "pct", "value": 0}))
    with pytest.raises(IntentValidationError):
        parse_intent(valid_payload(stop={"type": "pct", "value": 1.5}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", 10),
        ("qty", 5),
        ("shares", 8),
        ("notional", 1000),
        ("limit_price", 150.0),
        ("order_type", "market"),
        ("order_class", "bracket"),
        ("broker_order_id", "brk-1"),
        ("idempotency_key", "idem-1"),
        ("filled_avg_price", 150.0),
        ("slippage_pct", 0.01),
        ("commission", 1.0),
        ("take_profit_price", 160.0),
        ("mandate", True),
        ("kill", False),
        ("live", True),
        ("allow_live", True),
    ],
)
def test_forbidden_l2_fields_rejected(field, value):
    payload = valid_payload()
    payload[field] = value
    with pytest.raises(IntentValidationError, match="forbids L2"):
        parse_intent(payload)


@pytest.mark.parametrize("status", ["draft", "risk_approved", "submitted", "filled", "rejected"])
def test_order_status_rejected_as_intent_status(status):
    with pytest.raises(IntentValidationError):
        parse_intent(valid_payload(status=status))


def test_wrong_schema_version_rejected():
    with pytest.raises(IntentValidationError, match="schema_version"):
        parse_intent(valid_payload(schema_version="order.v0"))


def test_conviction_tier_closed_set():
    with pytest.raises(IntentValidationError, match="conviction_tier"):
        parse_intent(valid_payload(conviction_tier="max"))
    for tier in ("low", "med", "high"):
        assert parse_intent(valid_payload(conviction_tier=tier)).conviction_tier == tier


def test_expiry_must_follow_as_of():
    with pytest.raises(IntentValidationError, match="expiry must be after as_of"):
        parse_intent(valid_payload(expiry=NOW.isoformat(), as_of=EXPIRY.isoformat()))


def test_is_consumable_respects_expiry_and_status():
    intent = parse_intent(valid_payload())
    assert intent.is_consumable(NOW + timedelta(minutes=1))
    assert intent.is_expired(EXPIRY)
    assert not intent.is_consumable(EXPIRY)
    consumed = intent.model_copy(update={"status": IntentStatus.CONSUMED})
    assert not consumed.is_consumable(NOW)
