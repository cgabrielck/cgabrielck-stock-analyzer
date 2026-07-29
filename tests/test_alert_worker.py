import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import alert_worker
from config import TelegramSettings


class FakeRepository:
    def __init__(self):
        self.deliveries = []

    def claim_pending_alert_deliveries(self):
        return [{"id": "ok"}, {"id": "fail"}]

    def record_alert_delivery(self, event_id, delivered, error=None):
        self.deliveries.append((event_id, delivered, error))


def test_delivery_records_success_and_failure(monkeypatch) -> None:
    repository = FakeRepository()
    monkeypatch.setattr(alert_worker, "get_telegram_settings", lambda: TelegramSettings(
        bot_token="token", chat_id="123",
    ))

    def fake_send(settings, event):
        if event["id"] == "fail":
            raise RuntimeError("temporary failure")

    monkeypatch.setattr(alert_worker, "send_alert_message", fake_send)

    assert alert_worker.deliver_pending_alerts(repository) == 1
    assert repository.deliveries[0] == ("ok", True, None)
    assert repository.deliveries[1][0:2] == ("fail", False)
    assert "temporary failure" in repository.deliveries[1][2]


def test_delivery_skips_database_claim_when_telegram_is_not_configured(monkeypatch) -> None:
    class UnexpectedRepository:
        def claim_pending_alert_deliveries(self):
            raise AssertionError("should not claim deliveries")

    monkeypatch.setattr(alert_worker, "get_telegram_settings", lambda: TelegramSettings(
        bot_token="", chat_id="",
    ))

    assert alert_worker.deliver_pending_alerts(UnexpectedRepository()) == 0
