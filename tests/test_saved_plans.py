import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from accounts.repository import InMemoryAccountRepository
from saved_plans import (
    alert_rule_data,
    build_alert_levels,
    build_option_alert_levels,
    build_saved_plan,
    is_saveable_plan,
    plan_changes,
)


def _result(stance="bullish", offset=0):
    bearish = stance == "bearish"
    neutral = stance == "neutral"
    entry = {"low": 99 + offset, "high": 101 + offset}
    trade = {
        "stance": stance,
        "action": "avoid_or_hedge" if bearish else "watch" if neutral else "buy",
        "position_side": "short_or_hedge" if bearish else "none" if neutral else "long",
        "stop_type": "buy_to_cover_stop" if bearish else "invalidation_only" if neutral else "sell_stop",
        "setup": "test", "entry_zone": entry,
        "confirmation_price": 98 + offset if bearish else 102 + offset,
        "stop_loss": 105 + offset if bearish else 95 + offset,
        "targets": [93 + offset, 87 + offset] if bearish else [107 + offset, 113 + offset],
        "risk_reward": [1.5, 2.5], "entry_reference": 99 + offset,
        "risk_per_share": 6, "atr_14": 2, "execution_window": "regular", "method": "deterministic",
        "decision_basis": {"stance": stance},
    }
    return {
        "ticker": "TEST", "short_term": {"score": 75, "view": stance},
        "long_term": {"score": 70, "view": stance}, "avoid": {"active": False},
        "trade_plan": trade,
        "technical": {"price": 100, "price_quote_time": "2026-07-23T12:00:00Z", "price_stale": False},
        "quant_score": 70, "risk_adjusted_score": 68, "provenance": {"technical": {"source": "test"}},
    }


def test_saved_plan_contains_authoritative_levels_and_strict_json() -> None:
    result = _result()
    result["technical"]["ema_21"] = float("nan")

    plan = build_saved_plan("test", result, "2026-07-23T12:01:00Z", "en")

    assert plan["ticker"] == "TEST"
    assert plan["decision"]["stance"] == "bullish"
    assert plan["levels"]["stop_loss"] == 95
    assert plan["market_snapshot"]["ema_21"] is None
    json.dumps(plan, allow_nan=False)


def test_unavailable_plan_cannot_be_saved() -> None:
    assert not is_saveable_plan({"trade_plan": {"action": "no_trade", "error": "price_unavailable"}})


def test_alert_directions_follow_bullish_and_bearish_semantics() -> None:
    bullish = build_alert_levels(_result("bullish")["trade_plan"])
    bearish = build_alert_levels(_result("bearish")["trade_plan"])
    neutral = build_alert_levels(_result("neutral")["trade_plan"])

    assert bullish["confirmation"]["comparison"] == "crosses_up"
    assert bullish["stop"]["comparison"] == "at_or_below"
    assert bullish["targets"][0]["comparison"] == "at_or_above"
    assert bearish["confirmation"]["comparison"] == "crosses_down"
    assert bearish["stop"]["comparison"] == "at_or_above"
    assert bearish["targets"][0]["comparison"] == "at_or_below"
    assert neutral["stop"]["watch_only"] is True


def test_versions_are_immutable_and_isolated_by_user() -> None:
    repository = InMemoryAccountRepository()
    alice = repository.register("alice", "1")
    bob = repository.register("bob", "2")
    first = build_saved_plan("TEST", _result(offset=0), "2026-07-23T12:00:00Z", "en")
    second = build_saved_plan("TEST", _result(offset=1), "2026-07-24T12:00:00Z", "en")

    saved_1 = repository.save_plan(alice.id, "TEST", first, first["analysis_timestamp"])
    saved_2 = repository.save_plan(alice.id, "TEST", second, second["analysis_timestamp"])

    assert saved_1.version == 1
    assert saved_2.version == 2
    assert repository.get_active_plan(alice.id, "TEST") == saved_2
    assert [version.version for version in repository.list_plan_versions(alice.id, "TEST")] == [2, 1]
    assert repository.get_active_plan(bob.id, "TEST") is None
    assert repository.list_plan_versions(alice.id, "TEST")[-1].plan_data["levels"]["entry_zone"]["low"] == 99

    first["levels"]["entry_zone"]["low"] = 0
    assert repository.list_plan_versions(alice.id, "TEST")[-1].plan_data["levels"]["entry_zone"]["low"] == 99


def test_plan_diff_reports_only_changed_execution_fields() -> None:
    first = build_saved_plan("TEST", _result(offset=0), "2026-07-23T12:00:00Z", "en")
    second = build_saved_plan("TEST", _result(offset=1), "2026-07-24T12:00:00Z", "en")

    fields = {change["field"] for change in plan_changes(first, second)}

    assert fields == {"entry_low", "entry_high", "confirmation", "stop", "target_1", "target_2"}


def test_plan_diff_reports_changed_option_contract_and_premiums() -> None:
    first = build_saved_plan("TEST", _result(), "2026-07-23T12:00:00Z", "en")
    second = json.loads(json.dumps(first))
    first["option_plan"] = {
        "contract": {"contract_symbol": "TEST_CALL_1"}, "expiry": "2026-08-21",
        "max_entry_premium": 2.5, "stop_premium": 1.6,
        "take_profit_premiums": [3.4, 4.2], "data_source": "yfinance_options",
        "delayed_data": False,
    }
    second["option_plan"] = {
        **first["option_plan"],
        "contract": {"contract_symbol": "TEST_CALL_2"},
        "max_entry_premium": 2.8,
    }

    fields = {change["field"] for change in plan_changes(first, second)}

    assert fields == {"option_contract", "option_entry"}


def test_confirmed_alert_rules_replace_previous_rules_and_enable_monitoring() -> None:
    repository = InMemoryAccountRepository()
    user = repository.register("alice", "1")
    plan_data = build_saved_plan("TEST", _result(), "2026-07-23T12:00:00Z", "en")
    plan = repository.save_plan(user.id, "TEST", plan_data, plan_data["analysis_timestamp"])

    rules = repository.replace_alert_rules(user.id, plan.plan_id, plan.version, [
        ("entry_zone", alert_rule_data(plan_data, "entry_zone")),
        ("stop", alert_rule_data(plan_data, "stop")),
    ])

    assert {rule.event_type for rule in rules} == {"entry_zone", "stop"}
    assert all(rule.monitoring_enabled for rule in rules)
    repository.replace_alert_rules(user.id, plan.plan_id, plan.version, [])
    assert repository.list_alert_rules(user.id, plan.plan_id) == []


def test_new_plan_version_invalidates_old_alert_rules() -> None:
    repository = InMemoryAccountRepository()
    user = repository.register("alice", "1")
    first_data = build_saved_plan("TEST", _result(), "2026-07-23T12:00:00Z", "en")
    first = repository.save_plan(user.id, "TEST", first_data, first_data["analysis_timestamp"])
    repository.replace_alert_rules(user.id, first.plan_id, first.version, [
        ("confirmation", alert_rule_data(first_data, "confirmation")),
    ])

    second_data = build_saved_plan("TEST", _result(offset=1), "2026-07-24T12:00:00Z", "en")
    repository.save_plan(user.id, "TEST", second_data, second_data["analysis_timestamp"])

    assert repository.list_alert_rules(user.id, first.plan_id) == []


def test_plan_outcomes_are_immutable_idempotent_and_user_scoped() -> None:
    repository = InMemoryAccountRepository()
    alice = repository.register("alice", "1")
    bob = repository.register("bob", "2")
    data = build_saved_plan("TEST", _result(), "2026-01-01T00:00:00Z", "en")
    plan = repository.save_plan(alice.id, "TEST", data, data["analysis_timestamp"])
    outcome_data = {"horizon_days": 5, "calculation_version": 1, "raw_return_pct": 3.0}

    first = repository.record_plan_outcome(alice.id, plan.plan_id, plan.version, outcome_data)
    second = repository.record_plan_outcome(alice.id, plan.plan_id, plan.version, {**outcome_data, "raw_return_pct": 9.0})

    assert first == second
    assert first.outcome_data["raw_return_pct"] == 3.0
    assert repository.list_plan_outcomes(bob.id, plan.plan_id) == []


def test_option_alerts_require_a_complete_positive_price_plan() -> None:
    complete = {
        "action": "buy_to_open",
        "option_type": "call",
        "expiry": "2026-09-18",
        "contract": {"contract_symbol": "AAPL260918C00200000", "strike": 200},
        "max_entry_premium": 3.5,
        "stop_premium": 2.0,
        "take_profit_premiums": [5.0, 7.0],
    }

    result = build_option_alert_levels(complete)

    assert result["entry"]["monitor_symbol"] == "AAPL260918C00200000"
    assert result["targets"][1]["price"] == 7.0
    assert build_option_alert_levels({**complete, "take_profit_premiums": [5.0]}) == {}
    assert build_option_alert_levels({**complete, "stop_premium": -1}) == {}


def test_option_exit_rules_remain_disabled_until_confirmed_entry() -> None:
    repository = InMemoryAccountRepository()
    user = repository.register("alice", "1")
    result = _result()
    result["options_plan"] = {
        "action": "buy_to_open", "option_type": "call", "expiry": "2026-09-18",
        "contract": {"contract_symbol": "LLY260918C01000000", "strike": 1000},
        "max_entry_premium": 3.5, "stop_premium": 2.0,
        "take_profit_premiums": [5.0, 7.0],
    }
    plan_data = build_saved_plan("LLY", result, "2026-07-30T00:00:00Z", "en")
    plan = repository.save_plan(user.id, "LLY", plan_data, plan_data["analysis_timestamp"])
    rules = repository.replace_alert_rules(user.id, plan.plan_id, plan.version, [
        (event_type, alert_rule_data(plan_data, event_type))
        for event_type in ("option_entry", "option_stop", "option_target_1", "option_target_2")
    ])

    enabled = {rule.event_type for rule in rules if rule.monitoring_enabled}
    position = repository.get_option_position(user.id, plan.plan_id, plan.version)

    assert enabled == {"option_entry"}
    assert position.lifecycle_state == "watching_entry"
