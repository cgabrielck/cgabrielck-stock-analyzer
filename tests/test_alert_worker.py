import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import alert_worker
from config import TelegramSettings


class FakeRepository:
    def __init__(self):
        self.deliveries = []

    def claim_pending_alert_deliveries(self, user_id):
        assert user_id == "user-1"
        return [{"id": "ok"}, {"id": "fail"}]

    def record_alert_delivery(self, event_id, delivered, error=None):
        self.deliveries.append((event_id, delivered, error))


def test_delivery_records_success_and_failure(monkeypatch) -> None:
    repository = FakeRepository()
    monkeypatch.setattr(alert_worker, "get_telegram_settings", lambda: TelegramSettings(
        bot_token="token", chat_id="123", owner_user_id="user-1",
    ))

    def fake_send(settings, event):
        if event["id"] == "fail":
            raise RuntimeError("temporary failure")

    monkeypatch.setattr(alert_worker, "send_alert_message", fake_send)

    assert alert_worker.deliver_pending_alerts(repository, "user-1") == 1
    assert repository.deliveries[0] == ("ok", True, None)
    assert repository.deliveries[1][0:2] == ("fail", False)
    assert "temporary failure" in repository.deliveries[1][2]


def test_delivery_skips_database_claim_when_telegram_is_not_configured(monkeypatch) -> None:
    class UnexpectedRepository:
        def claim_pending_alert_deliveries(self, user_id):
            raise AssertionError("should not claim deliveries")

    monkeypatch.setattr(alert_worker, "get_telegram_settings", lambda: TelegramSettings(
        bot_token="", chat_id="", owner_user_id="",
    ))

    assert alert_worker.deliver_pending_alerts(UnexpectedRepository()) == 0


class EvaluationRepository:
    def __init__(self, rules):
        self.rules = rules
        self.evaluations = []

    def list_enabled_alerts(self, user_id=None):
        return self.rules

    def record_alert_evaluation(self, *args):
        self.evaluations.append(args)

    def claim_pending_alert_deliveries(self, user_id):
        return []


def test_option_entry_uses_adapter_and_records_agent_audit(monkeypatch) -> None:
    rule = {
        "id": "rule-1", "ticker": "LLY", "event_type": "option_entry",
        "last_price": 4.0, "armed": True,
        "rule_data": {
            "instrument_type": "option", "monitor_symbol": "LLY_CALL",
            "expiry": "2026-09-18", "price": 3.5, "comparison": "at_or_below",
        },
    }
    repository = EvaluationRepository([rule])
    quote_time = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(alert_worker, "fetch_option_quote", lambda *args: {
        "available": True, "price": 3.4, "quote_time": quote_time, "stale": False,
        "source": "option_chain", "session": "option_market", "bid": 3.2, "ask": 3.4,
        "spread_pct": 6.1, "contract_symbol": "LLY_CALL", "volume": 50,
        "open_interest": 200, "agent_trace": [{"stage": "quote_fetch", "status": "done"}],
    })
    monkeypatch.setattr(alert_worker, "deliver_pending_alerts", lambda repository, user_id: 0)

    result = alert_worker.check_alerts(repository, "user-1")

    assert result["triggered"] == 1
    assert result["evaluated"] == 1
    event_data = repository.evaluations[0][-1]
    assert event_data["ask"] == 3.4
    assert event_data["risk_judge"]["hard_gate_passed"] is True
    assert event_data["agent_trace"][0]["stage"] == "quote_fetch"


def test_rejected_option_quote_cannot_record_or_trigger(monkeypatch) -> None:
    rule = {
        "id": "rule-1", "ticker": "LLY", "event_type": "option_entry",
        "last_price": None, "armed": True,
        "rule_data": {
            "instrument_type": "option", "monitor_symbol": "LLY_CALL",
            "expiry": "2026-09-18", "price": 3.5, "comparison": "at_or_below",
        },
    }
    repository = EvaluationRepository([rule])
    monkeypatch.setattr(alert_worker, "fetch_option_quote", lambda *args: {
        "available": False, "price": None, "quote_time": None, "stale": True,
        "stale_reason": "stale_option_trade",
    })
    monkeypatch.setattr(alert_worker, "deliver_pending_alerts", lambda repository, user_id: 0)

    result = alert_worker.check_alerts(repository, "user-1")

    assert result["stale"] == 1
    assert result["triggered"] == 0
    assert repository.evaluations == []


def test_stock_alert_path_does_not_call_option_adapter(monkeypatch) -> None:
    rule = {
        "id": "rule-stock", "ticker": "LLY", "event_type": "target_1",
        "last_price": 99.0, "armed": True,
        "rule_data": {"price": 100.0, "comparison": "at_or_above"},
    }
    repository = EvaluationRepository([rule])
    quote_time = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(alert_worker, "get_latest_quote", lambda stock: {
        "price": 101.0, "quote_time": quote_time, "stale": False,
        "source": "stock_quote", "session": "regular",
    })
    monkeypatch.setattr(
        alert_worker, "fetch_option_quote",
        lambda *args: (_ for _ in ()).throw(AssertionError("option adapter called for stock")),
    )
    monkeypatch.setattr(alert_worker, "deliver_pending_alerts", lambda repository, user_id: 0)

    result = alert_worker.check_alerts(repository, "user-1")

    assert result["triggered"] == 1
    assert repository.evaluations[0][-1]["instrument_type"] == "stock"
