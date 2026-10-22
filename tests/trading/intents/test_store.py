"""JSONIntentStore CRUD + persistence."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.trading.intents.models import IntentStatus, IntentValidationError, parse_intent
from backend.trading.intents.store import IntentConflictError, JSONIntentStore
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


def test_stale_instance_save_does_not_wipe_other_intent(tmp_path: Path):
    path = str(tmp_path / "intents.json")
    store_a = JSONIntentStore(filepath=path)
    store_a.save(_intent(intent_id="keep-a", symbol="AAA"))
    store_b = JSONIntentStore(filepath=path)
    store_b.save(_intent(intent_id="keep-b", symbol="BBB"))
    store_a.save(_intent(intent_id="keep-a", symbol="AAA", conviction_tier="low"))
    reloaded = JSONIntentStore(filepath=path)
    assert reloaded.get("keep-a") is not None
    assert reloaded.get("keep-b") is not None
    assert reloaded.get("keep-a").conviction_tier == "low"


def test_concurrent_saves_all_persist(tmp_path: Path):
    path = str(tmp_path / "intents.json")
    errors: list[BaseException] = []

    def write(idx: int) -> None:
        try:
            store = JSONIntentStore(filepath=path)
            store.save(_intent(intent_id=f"c-{idx}", symbol=f"S{idx}"))
        except BaseException as exc:  # noqa: BLE001 — collect any worker failure
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    reloaded = JSONIntentStore(filepath=path)
    ids = {row.intent_id for row in reloaded.list(limit=100)}
    assert {f"c-{i}" for i in range(8)} <= ids


def test_concurrent_active_same_symbol_side_is_unique(tmp_path: Path):
    path = str(tmp_path / "intents.json")
    errors: list[BaseException] = []

    def write(idx: int) -> None:
        try:
            store = JSONIntentStore(filepath=path)
            store.save(_intent(intent_id=f"dup-{idx}", symbol="AAPL"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    reloaded = JSONIntentStore(filepath=path)
    actives = reloaded.list(status="active", symbol="AAPL", limit=100)
    assert len(actives) == 1


def test_save_refuses_to_revive_consumed_without_new_id(tmp_path: Path):
    store = JSONIntentStore(filepath=str(tmp_path / "intents.json"))
    store.save(_intent(intent_id="once"))
    store.set_status("once", IntentStatus.CONSUMED)
    with pytest.raises(IntentConflictError, match="cannot revive"):
        store.save(_intent(intent_id="once", status="active"))
    assert store.get("once").status is IntentStatus.CONSUMED
    replacement = store.save(_intent(intent_id="once-new", status="active"))
    assert replacement.status is IntentStatus.ACTIVE
    assert store.get("once").status is IntentStatus.CONSUMED


def test_claim_then_cancel_is_conflict(tmp_path: Path):
    store = JSONIntentStore(filepath=str(tmp_path / "intents.json"))
    store.save(_intent(intent_id="cas"))
    claimed = store.claim_for_consume("cas")
    assert claimed is not None
    assert claimed.status is IntentStatus.CONSUMED
    with pytest.raises(IntentConflictError, match="cannot cancel"):
        store.set_status("cas", IntentStatus.CANCELLED)
    assert store.get("cas").status is IntentStatus.CONSUMED


def test_double_cancel_is_conflict(tmp_path: Path):
    store = JSONIntentStore(filepath=str(tmp_path / "intents.json"))
    store.save(_intent(intent_id="dbl"))
    assert store.set_status("dbl", IntentStatus.CANCELLED).status is IntentStatus.CANCELLED
    with pytest.raises(IntentConflictError, match="cannot cancel"):
        store.set_status("dbl", IntentStatus.CANCELLED)


def test_cancel_then_claim_returns_none(tmp_path: Path):
    store = JSONIntentStore(filepath=str(tmp_path / "intents.json"))
    store.save(_intent(intent_id="cas2"))
    cancelled = store.set_status("cas2", IntentStatus.CANCELLED)
    assert cancelled is not None
    assert cancelled.status is IntentStatus.CANCELLED
    assert store.claim_for_consume("cas2") is None
    assert store.get("cas2").status is IntentStatus.CANCELLED


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
