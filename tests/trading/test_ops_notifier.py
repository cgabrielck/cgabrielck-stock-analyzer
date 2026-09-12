from types import SimpleNamespace

from backend.config import TelegramSettings
from backend.trading.ops_notifier import format_trade_notice, notify_trade_decision, notify_trade_fill


def test_placeholder_telegram_is_not_ops_configured():
    settings = TelegramSettings(
        bot_token="replace-with-your-bot-token",
        chat_id="replace-with-your-chat-id",
        owner_user_id="replace-with-your-stock-analyzer-user-uuid",
    )
    assert settings.ops_configured is False
    assert settings.configured is False


def test_real_token_ops_configured_without_owner():
    settings = TelegramSettings(bot_token="123456:AA-real-looking-token", chat_id="987654321", owner_user_id="")
    assert settings.ops_configured is True
    assert settings.configured is False


def test_format_decision_includes_stop_and_predicted_gain():
    text = format_trade_notice(
        kind="decision",
        symbol="AAPL",
        side="buy",
        quantity=10,
        price=100,
        mode="paper",
        stop_loss=95,
        take_profit=110,
        strategy="stable",
        reason="RSI oversold",
        status="submitted",
    )
    assert "Paper auto decided to BUY" in text
    assert "Stop loss:" in text and "$95.00" in text
    assert "Predicted gain:" in text and "10.0%" in text and "$100.00" in text
    assert "R/R:" in text
    assert "stable" in text


def test_notify_skips_when_not_configured(monkeypatch):
    sent = []
    monkeypatch.setattr("backend.trading.ops_notifier.requests.post", lambda *a, **k: sent.append(1))
    settings = SimpleNamespace(ops_configured=False, configured=False, bot_token="x", chat_id="1")
    assert notify_trade_decision(settings, symbol="AAPL", side="buy", quantity=1, price=10) is False
    assert notify_trade_fill(settings, symbol="AAPL", side="buy", quantity=1, price=10) is False
    assert sent == []
