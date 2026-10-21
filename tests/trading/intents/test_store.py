"""JSONIntentStore CRUD + persistence."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.trading.intents.models import IntentStatus, IntentValidationError, parse_intent
from backend.trading.intents.store import JSONIntentStore
from backend.trading.intents.writer import upsert_active_intent

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
EXPIRY = NOW + timedelta(hours=2)


def _intent(**overrides):
    data = {
        "schema_version": "intent.v0",
        "intent_id": "int-store-1",
        "symbol": "MSFT",
        "side": "buy",
        "stop": {"type": "pct", "value": 0.08},
        "conviction_tier": "high",
        "expiry": EXPIRY,
        "as_of": NOW,
        "status": "active",
    }
    data.update(overrides)
    return parse_intent(data)


def test_save_get_list(tmp_path: Path):
    store = JSONIntentStore(filepath=str(tmp_path / "intents.json"))
    saved = store.save(_intent())
    found = store.get(saved.intent_id)
    assert found is not None
    assert found.symbol == "MSFT"
    listed = store.list(status="active", symbol="MSFT")
    assert len(listed) == 1
    assert listed[0].intent_id == saved.intent_id


def test_persistence_across_instances(tmp_path: Path):
    path = str(tmp_path / "intents.json")
    store = JSONIntentStore(filepath=path)
    store.save(_intent(intent_id="persist-1"))
    reloaded = JSONIntentStore(filepath=path)
    found = reloaded.get("persist-1")
    assert found is not None
    assert found.conviction_tier == "high"
    disk = json.loads(Path(path).read_text(encoding="utf-8"))
    assert disk["schema_version"] == "intent.v0"
    assert "quantity" not in disk["intents"][0]


def test_set_status_transitions(tmp_path: Path):
    store = JSONIntentStore(filepath=str(tmp_path / "intents.json"))
    store.save(_intent(intent_id="st-1"))
    updated = store.set_status("st-1", IntentStatus.CONSUMED)
    assert updated is not None
    assert updated.status is IntentStatus.CONSUMED
    assert store.get("st-1").status is IntentStatus.CONSUMED


def test_skips_invalid_rows_on_load(tmp_path: Path):
    path = tmp_path / "intents.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "intent.v0",
                "intents": [
                    {
                        "schema_version": "intent.v0",
                        "intent_id": "bad",
                        "symbol": "NVDA",
                        "side": "buy",
                        "stop": {"type": "pct", "value": 0.1},
                        "conviction_tier": "low",
                        "expiry": EXPIRY.isoformat(),
                        "as_of": NOW.isoformat(),
                        "status": "active",
                        "quantity": 99,
                    },
                    _intent(intent_id="good").model_dump_json_dict(),
                ],
            }
        ),
        encoding="utf-8",
    )
    store = JSONIntentStore(filepath=str(path))
    assert store.get("bad") is None
    assert store.get("good") is not None


def test_upsert_active_cancels_prior_same_symbol_side(tmp_path: Path):
    store = JSONIntentStore(filepath=str(tmp_path / "intents.json"))
    first = upsert_active_intent(
        store,
        symbol="AAPL",
        side="buy",
        conviction_tier="low",
        expiry=EXPIRY,
        as_of=NOW,
        stop={"type": "pct", "value": 0.1},
        intent_id="first",
    )
    second = upsert_active_intent(
        store,
        symbol="AAPL",
        side="buy",
        conviction_tier="med",
        expiry=EXPIRY,
        as_of=NOW,
        stop={"type": "pct", "value": 0.12},
        intent_id="second",
    )
    assert store.get(first.intent_id).status is IntentStatus.CANCELLED
    assert store.get(second.intent_id).status is IntentStatus.ACTIVE
    assert second.conviction_tier == "med"


def test_upsert_rejects_veto_codes(tmp_path: Path):
    store = JSONIntentStore(filepath=str(tmp_path / "intents.json"))
    with pytest.raises(IntentValidationError, match="veto_codes"):
        upsert_active_intent(
            store,
            symbol="AAPL",
            side="buy",
            conviction_tier="med",
            expiry=EXPIRY,
            as_of=NOW,
            stop={"type": "pct", "value": 0.1},
            veto_codes=["CALENDAR_BLACKOUT"],
        )
