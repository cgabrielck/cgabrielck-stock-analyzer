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
    assert parse_command("跟掃描")[0] == "follow_scan"
    assert parse_command("research_list")[0] == "follow_scan"


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
    assert "待買" in labels
    assert "理由" in labels
    assert "日報" in labels
    assert "買" not in labels
    assert "買入" not in labels


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
    monkeypatch.setattr(telegram_inbox, "working_buy_orders", lambda: [])
    monkeypatch.setattr(
        "backend.api.paper_performance.digest_status_line",
        lambda digest=None: "今日成交 0 · 排隊 0（未成交不算封印）。點「日報」看完整摘要。",
    )
    text = telegram_inbox._cmd_status()
    assert "為何沒買" in text
    assert "過期" in text
    assert "RSI" in text or "基本面" in text
    assert "跟掃描" in text
    assert "日報" in text
    assert "未成交不算封印" in text


def test_follow_scan_restarts_research_list(monkeypatch):
    from backend.trading import telegram_inbox

    monkeypatch.setattr(telegram_inbox, "_set_saved_strategy", lambda *a, **k: {"strategy": "research_list"})
    monkeypatch.setattr(
        "backend.api.worker_ctl.status",
        lambda: {"running": True, "strategy": "breakout"},
    )
    monkeypatch.setattr(
        "backend.api.worker_ctl.restart",
        lambda strategy, interval=60: {"running": True, "strategy": strategy},
    )
    monkeypatch.setattr(
        telegram_inbox,
        "_scan_picks_line",
        lambda: "掃描 新鮮 · as-of 2026-09-14 19:37:47 · 前五 TSM, NVDA",
    )
    text = telegram_inbox.handle_command("follow_scan")
    assert "research_list" in text
    assert "TSM" in text
    assert "風控" in text
    assert "買入" not in text or "不會" in text
    assert "不會改成 defensive_gld" in text


def test_start_auto_uses_saved_strategy_not_stable(monkeypatch):
    from backend.trading import telegram_inbox

    seen = {"start": 0, "restart": 0}

    def _restart(strategy, interval=60):
        seen["restart"] += 1
        seen["strategy"] = strategy
        return {"running": True, "strategy": strategy, "already_running": False}

    def _start(strategy, interval=60):
        seen["start"] += 1
        return {"running": True, "strategy": strategy, "already_running": True}

    monkeypatch.setattr(
        telegram_inbox,
        "_load_ai_mode",
        lambda: {"strategy": "research_list", "entry_threshold": 70},
    )
    monkeypatch.setattr(telegram_inbox, "_set_saved_strategy", lambda *a, **k: None)
    monkeypatch.setattr("backend.api.worker_ctl.restart", _restart)
    monkeypatch.setattr("backend.api.worker_ctl.start", _start)
    monkeypatch.setattr(telegram_inbox, "_scan_picks_line", lambda: "掃描 新鮮 · 前五 TSM")
    text = telegram_inbox._cmd_start_auto()
    assert seen["restart"] == 1
    assert seen["start"] == 0
    assert seen.get("strategy") == "research_list"
    assert "research_list" in text
    assert "stable" not in text


def test_start_auto_reports_timeout(monkeypatch):
    from backend.trading import telegram_inbox

    enabled = {}

    def _save(*_a, **k):
        enabled.update(k)

    monkeypatch.setattr(telegram_inbox, "_load_ai_mode", lambda: {"strategy": "research_list"})
    monkeypatch.setattr(telegram_inbox, "_set_saved_strategy", _save)

    def _restart(strategy, interval=60):
        raise RuntimeError("Worker failed to start (code None).")

    monkeypatch.setattr("backend.api.worker_ctl.restart", _restart)
    text = telegram_inbox._cmd_start_auto()
    assert "啟動失敗" in text
    assert "failed to start" in text
    assert "worker_enabled" not in enabled


def test_stop_auto_reports_failure_when_stop_raises(monkeypatch):
    from backend.trading import telegram_inbox

    enabled = {}

    def _save(*_a, **k):
        enabled.update(k)

    monkeypatch.setattr(telegram_inbox, "_load_ai_mode", lambda: {"strategy": "breakout"})
    monkeypatch.setattr(telegram_inbox, "_set_saved_strategy", _save)

    def _stop():
        raise RuntimeError("Worker did not stop; leftover pids [2152]")

    monkeypatch.setattr("backend.api.worker_ctl.stop", _stop)
    text = telegram_inbox._cmd_stop_auto()
    assert "停止失敗" in text
    assert "2152" in text
    assert enabled.get("worker_enabled") is not False


def test_parse_pending_and_why():
    assert parse_command("待買")[0] == "pending"
    assert parse_command("理由") == ("why", "")
    assert parse_command("為何 GOOGL") == ("why", "GOOGL")
    assert parse_command("why NVDA") == ("why", "NVDA")


def test_cmd_pending_lists_limit_order(monkeypatch):
    from backend.trading import telegram_inbox

    monkeypatch.setattr(
        telegram_inbox,
        "working_buy_orders",
        lambda: [
            {
                "id": "abc",
                "symbol": "GOOGL",
                "qty": 28,
                "limit_price": 349.39,
                "status": "new",
                "submitted_at": "2026-09-15 08:00:16+00:00",
            }
        ],
    )
    text = telegram_inbox.handle_command("pending")
    assert "GOOGL" in text
    assert "349.39" in text
    assert "28" in text
    assert "new" in text
    assert "不會從這裡下單" in text


def test_cmd_why_explains_scan_pick_and_open_order(monkeypatch):
    from backend.trading import telegram_inbox

    monkeypatch.setattr(
        telegram_inbox,
        "_scan_pick",
        lambda symbol: {
            "ticker": "GOOGL",
            "name": "谷歌",
            "rank": 5,
            "in_top5": True,
            "top5": ["TSM", "ISRG", "NVDA", "MSFT", "GOOGL"],
            "risk_adjusted_score": 78.2,
            "growth_score": 80,
            "llm_key_signal": "bullish",
            "reasoning": "成長與趨勢分數較高。",
            "stale": False,
            "as_of": "2026-09-14T19:37:47",
        }
        if str(symbol).upper() == "GOOGL"
        else None,
    )
    monkeypatch.setattr(telegram_inbox, "_worker_skip_for", lambda symbol: "order_open")
    monkeypatch.setattr(
        telegram_inbox,
        "working_buy_orders",
        lambda: [{"symbol": "GOOGL", "qty": 28, "limit_price": 349.39, "status": "new"}],
    )
    text = telegram_inbox.handle_command("why", "GOOGL")
    assert "GOOGL" in text
    assert "第 5 名" in text
    assert "349.39" in text
    assert "毫秒" in text


def test_follow_scan_never_starts_defensive_gld(monkeypatch):
    from backend.trading import telegram_inbox

    seen = {}

    def _restart(strategy, interval=60):
        seen["strategy"] = strategy
        return {"running": True, "strategy": strategy}

    monkeypatch.setattr(telegram_inbox, "_set_saved_strategy", lambda *a, **k: {"strategy": "research_list"})
    monkeypatch.setattr("backend.api.worker_ctl.status", lambda: {"running": True, "strategy": "defensive_gld"})
    monkeypatch.setattr("backend.api.worker_ctl.restart", _restart)
    monkeypatch.setattr(telegram_inbox, "_scan_picks_line", lambda: "掃描 新鮮 · 前五 TSM")
    text = telegram_inbox.handle_command("follow_scan")
    assert seen.get("strategy") == "research_list"
    assert "research_list" in text
    assert "不會改成 defensive_gld" in text


def test_help_keeps_follow_scan_as_research_list():
    from backend.trading.telegram_inbox import handle_command

    text = handle_command("help")
    assert "research_list" in text
    assert "跟掃描" in text
    assert "defensive_gld" in text
    assert "AI 模式" in text


def test_parse_defensive_gld_is_not_follow_scan():
    assert parse_command("defensive_gld")[0] != "follow_scan"
    assert parse_command("跟掃描")[0] == "follow_scan"


def test_unknown_universe_ticker_routes_to_why(monkeypatch):
    from backend.trading import telegram_inbox

    monkeypatch.setattr(telegram_inbox, "_why_one", lambda symbol: f"trace-{symbol}")
    text = telegram_inbox.handle_update(
        {"message": {"chat": {"id": 111}, "text": "GOOGL"}},
        "111",
    )
    assert text == "trace-GOOGL"


def test_parse_digest():
    assert parse_command("日報")[0] == "digest"
    assert parse_command("digest")[0] == "digest"


def test_cmd_digest_filled_vs_pending(monkeypatch):
    from backend.trading import telegram_inbox

    monkeypatch.setattr(
        "backend.api.paper_performance.build_daily_digest",
        lambda **k: {
            "as_of_date": "2026-09-15",
            "filled": {"count": 1, "symbols": ["AAPL"]},
            "pending": {"count": 1, "symbols": ["GOOGL"], "not_a_seal": True},
            "skips": {"summary_zh": "RSI 尚未超賣／無 Connors 回撤 ×3"},
            "benchmark": {"alpha_net_of_costs_pct": -0.5, "beats_spy_net": False},
            "scan": {"as_of": "2026-09-15T20:00:00+00:00", "stale": False, "available": True, "top5": ["AAPL"]},
            "strategy": "breakout",
            "not_a_seal": True,
            "disclaimer_zh": "未成交限價單不算封印",
        },
    )
    text = telegram_inbox.handle_command("digest")
    assert "AAPL" in text
    assert "GOOGL" in text
    assert "未成交" in text
    assert "SPY" in text
    assert "不會從這裡下單" not in text or "紙上" in text
