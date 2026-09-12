"""Thin Slice B — paper vs SPY report + post-close digest."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import backend.api.paper_performance as pp
import backend.api.scan_scheduler as sched


def test_build_paper_report_empty_book(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "_load_paper_orders", lambda: [])
    monkeypatch.setattr(pp, "DATA_DIR", tmp_path)

    class FakeSummary:
        equity = 100000.0
        portfolio_value = 100000.0
        day_pnl = 12.5

    class FakeBroker:
        def get_account_summary(self):
            return FakeSummary()

    monkeypatch.setattr(
        "backend.trading.alpaca_broker.AlpacaBroker",
        FakeBroker,
    )
    report = pp.build_paper_report(persist=False, initial_capital=100000)
    assert report["available"] is True
    assert report["empty_book"] is True
    assert report["benchmark"]["assumed_cost_bps"] == pp.DEFAULT_COST_BPS
    assert report["benchmark"]["alpha_net_of_costs_pct"] is None
    assert report["benchmark"]["beats_spy_net"] is None
    assert "disclaimer" in report


def test_build_paper_report_with_fills(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "DATA_DIR", tmp_path)
    orders = [
        {
            "symbol": "AAPL",
            "side": "buy",
            "status": "filled",
            "quantity": 10,
            "filled_avg_price": 100,
            "filled_at": "2026-08-01T15:00:00+00:00",
        },
        {
            "symbol": "AAPL",
            "side": "sell",
            "status": "filled",
            "quantity": 10,
            "filled_avg_price": 110,
            "filled_at": "2026-08-15T15:00:00+00:00",
        },
    ]
    monkeypatch.setattr(pp, "_load_paper_orders", lambda: orders)

    # Avoid live SPY / Alpaca in unit test
    from backend.trading.performance import tracker as tr

    monkeypatch.setattr(tr, "fetch_spy_returns", lambda *a, **k: None)
    monkeypatch.setattr(
        "backend.trading.alpaca_broker.AlpacaBroker",
        lambda: (_ for _ in ()).throw(RuntimeError("skip")),
    )

    report = pp.build_paper_report(persist=True, initial_capital=100000)
    assert report["trading"]["num_trades"] >= 1
    assert (tmp_path / "performance_paper.json").exists()
    assert report["benchmark"]["alpha_net_of_costs_pct"] is not None


def test_post_close_digest_once_per_day(monkeypatch):
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 9, 14, 16, 10, tzinfo=et)
    monkeypatch.setattr(sched, "_now_et", lambda: now)

    sent = {"n": 0}

    class FakeSettings:
        ops_configured = True

    monkeypatch.setattr(sched, "_telegram_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        "backend.trading.ops_notifier.send_plain",
        lambda *a, **k: sent.__setitem__("n", sent["n"] + 1) or True,
    )
    monkeypatch.setattr(
        "backend.api.research_jobs.latest_scan",
        lambda: {
            "ts": "2026-09-14T20:00:00+00:00",
            "stale": False,
            "available": True,
            "top5_tickers": ["AAPL"],
        },
    )
    monkeypatch.setattr(
        "backend.api.worker_ctl.status",
        lambda: {"skip_counts": {"rsi_not_oversold": 3}, "strategy": "stable"},
    )
    monkeypatch.setattr(
        "backend.api.paper_performance.build_paper_report",
        lambda **k: {"benchmark": {"alpha_net_of_costs_pct": -1.2}},
    )
    monkeypatch.setattr(
        "backend.trading.alpaca_broker.AlpacaBroker",
        lambda: (_ for _ in ()).throw(RuntimeError("skip")),
    )

    with sched._lock:
        sched._state["last_digest_at"] = None

    assert sched.send_post_close_digest() is True
    assert sent["n"] == 1
    assert sched.send_post_close_digest() is False  # same day
    assert sent["n"] == 1
    assert sched.send_post_close_digest(force=True) is True
    assert sent["n"] == 2
