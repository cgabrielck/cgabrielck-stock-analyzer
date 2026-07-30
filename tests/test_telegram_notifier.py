import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import telegram_notifier
from config import TelegramSettings
from telegram_notifier import format_alert_message, send_alert_message


def test_telegram_requires_complete_settings() -> None:
    with pytest.raises(RuntimeError, match="incomplete"):
        send_alert_message(TelegramSettings(bot_token="", chat_id="", owner_user_id=""), {})


def test_option_message_escapes_html_and_contains_disclaimer() -> None:
    message = format_alert_message({
        "ticker": "AAPL<script>",
        "event_type": "option_entry",
        "price": 3.5,
        "quote_time": "2026-07-29T12:00:00Z",
        "event_data": {
            "instrument_type": "option",
            "monitor_symbol": "AAPL260918C00200000",
            "option_type": "call",
        },
    })

    assert "Stock Analyzer: OPTION ENTRY OPPORTUNITY" in message
    assert "AAPL&lt;script&gt;" in message
    assert "AAPL260918C00200000" in message
    assert "Not an order" in message
    assert "OPTION ENTRY OPPORTUNITY" in message
    assert "No fill or position was recorded" in message
    assert "before stop and target alerts activate" in message


def test_send_message_posts_to_telegram_api(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, timeout):
        captured.update(url=url, json=json, timeout=timeout)
        return type("Response", (), {"ok": True, "status_code": 200})()

    monkeypatch.setattr(telegram_notifier.requests, "post", fake_post)

    send_alert_message(TelegramSettings(bot_token="secret-token", chat_id="123", owner_user_id="user-1"), {
        "ticker": "AAPL", "event_type": "target_1", "price": 220,
    })

    assert captured["url"].endswith("/botsecret-token/sendMessage")
    assert captured["json"]["chat_id"] == "123"
    assert captured["json"]["parse_mode"] == "HTML"
    assert captured["timeout"] == 20


def test_request_error_does_not_expose_bot_token(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise requests.ConnectionError("network error")

    monkeypatch.setattr(telegram_notifier.requests, "post", fail)

    with pytest.raises(RuntimeError, match="Telegram request failed") as error:
        send_alert_message(TelegramSettings(bot_token="secret-token", chat_id="123", owner_user_id="user-1"), {})

    assert "secret-token" not in str(error.value)
