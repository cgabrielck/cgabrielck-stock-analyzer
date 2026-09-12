from types import SimpleNamespace

from backend.trading.telegram_inbox import allowed_chat, handle_update, parse_command


def test_parse_start_is_help_not_auto():
    assert parse_command("/start")[0] == "help"
    assert parse_command("幫助")[0] == "help"
    assert parse_command("狀態")[0] == "status"
    assert parse_command("啟動")[0] == "start_auto"
    assert parse_command("停止")[0] == "stop_auto"
    assert parse_command("急停")[0] == "halt"
    assert parse_command("選單")[0] == "help"


def test_parse_rejects_text_orders():
    assert parse_command("買 AAPL")[0] == "reject_order"
    assert parse_command("/buy NVDA")[0] == "reject_order"


def test_wrong_chat_is_ignored():
    update = {"update_id": 1, "message": {"chat": {"id": 999}, "text": "狀態"}}
    assert handle_update(update, "123456") is None
    assert allowed_chat(123456, "123456") is True


def test_help_from_owner_chat():
    update = {"update_id": 2, "message": {"chat": {"id": 111}, "text": "幫助"}}
    text = handle_update(update, "111")
    assert text and ("選單" in text or "狀態" in text)
    assert "風控" in text


def test_reply_keyboard_has_ops_not_buy():
    from backend.trading.telegram_inbox import reply_keyboard

    kb = reply_keyboard()
    labels = [btn["text"] for row in kb["keyboard"] for btn in row]
    assert labels == ["狀態", "持倉", "啟動", "停止", "急停", "恢復", "掃描", "選單"]
    assert "買" not in "".join(labels)


def test_cmd_status_includes_skip_reasons(monkeypatch):
    from backend.trading import telegram_inbox

    monkeypatch.setattr(
        "backend.api.worker_ctl.status",
        lambda: {
            "running": True,
            "stale": False,
            "strategy": "stable",
            "scan_stale": True,
            "skip_counts": {"rsi_not_oversold": 12, "fund_lt_65": 8},
            "universe_cap": 74,
            "universe_size": 74,
            "fetched": 40,
            "last_signals": [],
        },
    )
    monkeypatch.setattr(
        "backend.api.research_jobs.latest_scan",
        lambda: {"available": True, "stale": True, "top5_tickers": ["TSM"]},
    )
    monkeypatch.setattr("backend.trading.safety.kill_switch.is_halted", lambda: False)
    text = telegram_inbox._cmd_status()
    assert "為何沒買" in text
    assert "過期" in text
    assert "RSI" in text or "基本面" in text
