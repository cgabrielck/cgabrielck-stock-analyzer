"""Slice D D4–D6: fail-cases for dishonest Desk labels and silent fallback."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.desk_display import (
    compose_performance_kpis,
    display_chips,
    orders_book,
    paper_vs_spy_risk,
    strategy_fallback_row,
)
from backend.trading.engine.order_manager import InMemoryOrderStore
from backend.trading.models import Order, OrderSide, OrderStatus, OrderType


ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "web" / "static" / "index.html"
APP_TSX = ROOT / "web" / "src" / "App.tsx"
USER_GUIDE = ROOT / "docs" / "CGAB_USER_GUIDE.md"


# --- D4: Worker linked chip -------------------------------------------------


def test_d4_account_loaded_is_not_worker_running():
    chips = display_chips({"running": False}, account_loaded=True)
    assert chips["account_loaded"] is True
    assert chips["account_label"] == "Account loaded"
    assert chips["worker_running"] is False
    assert chips["auto_on"] is False
    assert chips["worker_tone"] != "on"
    assert "linked" not in chips["worker_label"].lower()
    assert chips["verified"] is False
    assert chips["live"] is False


def test_d4_heartbeat_unverified_is_not_green_or_linked():
    chips = display_chips(
        {
            "running": True,  # dishonest running bit must still lose to unverified
            "unverified": True,
            "degraded": True,
            "process_evidence": "heartbeat_unverified",
        },
        account_loaded=True,
    )
    assert chips["auto_on"] is False
    assert chips["worker_running"] is False
    assert chips["worker_tone"] == "warn"
    assert chips["worker_label"] == "Worker unverified"
    assert "linked" not in chips["worker_label"].lower()
    assert chips["account_label"] == "Account loaded"


def test_d4_pid_alive_may_show_running():
    chips = display_chips(
        {"running": True, "unverified": False, "process_evidence": "pid_alive"},
        account_loaded=True,
    )
    assert chips["worker_running"] is True
    assert chips["auto_on"] is True
    assert chips["worker_label"] == "Worker running"
    assert chips["worker_tone"] == "on"


def test_d4_ui_does_not_treat_account_as_worker_linked():
    tsx = APP_TSX.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert 'Worker {account ? "linked"' not in tsx
    assert "Worker linked" not in tsx
    assert "accountChipLabel" in tsx
    assert "Worker unverified" in tsx
    assert "Account loaded" in tsx
    assert "function workerProcessOn" in html
    assert "heartbeat_unverified" in html
    assert "worker.unverified" in html


# --- D5: GET /api/orders silent local-store fallback ------------------------


def test_d5_local_store_envelope_is_degraded():
    book = orders_book([{"id": "loc-1", "symbol": "AAPL"}], source="local_store", degraded=True, unverified=True)
    assert book["source"] == "local_store"
    assert book["degraded"] is True
    assert book["unverified"] is True
    assert book["verified"] is False
    assert book["live"] is False
    assert book["count"] == 1


def test_d5_broker_envelope_is_not_silent_fallback():
    book = orders_book([{"id": "brk-1"}], source="broker", degraded=False, unverified=False)
    assert book["source"] == "broker"
    assert book["degraded"] is False
    assert book["unverified"] is False
    assert book["verified"] is False
    assert book["live"] is False


@pytest.fixture
def honesty_api(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.trading.telegram_inbox.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.scan_scheduler.start_background", lambda: (lambda: None))
    monkeypatch.setenv("APCA_PAPER", "true")
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "unit-test-operator")
    broker = MagicMock()
    store = InMemoryOrderStore()
    from backend.api import app as app_mod

    monkeypatch.setattr(app_mod, "_broker", broker)
    monkeypatch.setattr(app_mod, "_store", store)
    with TestClient(app_mod.app, raise_server_exceptions=False) as client:
        client.headers.update({"X-Desk-Operator-Token": "unit-test-operator"})
        yield {"client": client, "broker": broker, "store": store}


def test_d5_get_orders_fallback_marks_degraded(honesty_api):
    """Fail-case: broker error must not look like a clean broker book."""
    honesty_api["broker"].api.get_orders.side_effect = RuntimeError("alpaca unavailable")
    honesty_api["store"].save_order(
        Order(
            id="loc-fallback-1",
            symbol="MSFT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2,
            status=OrderStatus.SUBMITTED,
            idempotency_key="idem-loc-1",
        )
    )
    res = honesty_api["client"].get("/api/orders")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "local_store"
    assert body["degraded"] is True
    assert body["unverified"] is True
    assert body.get("verified") is not True
    assert body.get("live") is not True
    assert any(row.get("id") == "loc-fallback-1" or row.get("symbol") == "MSFT" for row in body["orders"])


def test_d5_get_orders_broker_success_is_labeled_broker(honesty_api):
    row = MagicMock()
    row.id = "brk-1"
    row.symbol = "AAPL"
    row.side = "buy"
    row.type = "market"
    row.qty = 1
    row.status = "accepted"
    row.limit_price = None
    row.filled_avg_price = None
    row.submitted_at = None
    honesty_api["broker"].api.get_orders.return_value = [row]
    res = honesty_api["client"].get("/api/orders?status=open")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "broker"
    assert body["degraded"] is False
    assert body["unverified"] is False
    assert body.get("verified") is not True
    assert body.get("live") is not True
    assert body["orders"][0]["id"] == "brk-1"


def test_d5_ui_surfaces_degraded_orders_source():
    html = INDEX.read_text(encoding="utf-8")
    tsx = APP_TSX.read_text(encoding="utf-8")
    assert "orders.degraded" in html
    assert "local_store" in html
    assert "not a clean broker book" in html
    assert "ordersBookDirty" in tsx
    assert "not a clean broker book" in tsx


# --- D6: KPI labeled backtest can show paper --------------------------------


def test_d6_paper_kpis_are_not_labeled_backtest():
    paper = {
        "empty_book": False,
        "source": "sqlite_orders",
        "trading": {"win_rate_pct": 40.0, "profit_factor": 1.1, "num_trades": 4},
        "risk": {"sharpe_ratio": 0.4, "max_drawdown_pct": 8.0},
        "pnl": {"total_return_pct": 3.2},
    }
    summary = {
        "win_rate_pct": 90.0,
        "profit_factor": 9.9,
        "sharpe_ratio": 2.5,
        "max_drawdown_pct": 1.0,
        "total_return_pct": 88.0,
        "num_trades": 200,
    }
    out = compose_performance_kpis(paper, summary)
    assert out["kpi_source"] == "paper"
    assert out["win_rate_pct"] == 40.0
    assert out["win_rate_pct"] != summary["win_rate_pct"]
    assert out["total_return_pct"] == 3.2
    assert out["verified"] is False
    assert out["live"] is False


def test_d6_empty_paper_falls_back_to_backtest_with_source():
    paper = {"empty_book": True, "trading": {"win_rate_pct": None, "num_trades": 0}}
    summary = {"win_rate_pct": 55.0, "num_trades": 12, "total_return_pct": 10.0}
    out = compose_performance_kpis(paper, summary)
    assert out["kpi_source"] == "backtest"
    assert out["win_rate_pct"] == 55.0
    assert out["num_trades"] == 12


def test_d6_no_evidence_is_none_not_backtest():
    out = compose_performance_kpis(None, {})
    assert out["kpi_source"] == "none"
    assert out["win_rate_pct"] is None


def test_d6_performance_api_labels_paper_not_backtest(honesty_api, monkeypatch, tmp_path):
    from backend.api import app as app_mod
    from backend.api import paper_performance

    paper = {
        "available": True,
        "empty_book": False,
        "source": "sqlite_orders",
        "trading": {"win_rate_pct": 33.0, "profit_factor": 1.2, "num_trades": 3, "turnover_pct": 10},
        "risk": {"sharpe_ratio": 0.2, "max_drawdown_pct": 5.0},
        "pnl": {"total_return_pct": 1.5},
        "benchmark": {"spy_return_pct": 2.0, "alpha_net_of_costs_pct": -0.5, "beats_spy_net": False},
        "disclaimer": "paper only",
    }
    monkeypatch.setattr(paper_performance, "load_cached_paper_report", lambda: None)
    monkeypatch.setattr(paper_performance, "build_paper_report", lambda persist=True: paper)
    monkeypatch.setattr(app_mod, "DATA_DIR", tmp_path)
    (tmp_path / "strategy_backtest_stable_summary.json").write_text(
        '{"win_rate_pct": 99.0, "total_return_pct": 77.0, "num_trades": 500}',
        encoding="utf-8",
    )
    res = honesty_api["client"].get("/api/performance")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kpi_source"] == "paper"
    assert body["win_rate_pct"] == 33.0
    assert body["win_rate_pct"] != 99.0
    assert body["backtest"]["win_rate_pct"] == 99.0
    assert body.get("verified") is not True
    assert body.get("live") is not True


def test_d6_ui_does_not_hardcode_backtest_over_paper_kpis():
    tsx = APP_TSX.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert "Win rate (backtest)" not in tsx
    assert "kpiSourceLabel" in tsx
    assert "Profit factor ({kpiSourceLabel(perf)})" in tsx
    assert "Max DD ({kpiSourceLabel(perf)})" in tsx
    assert "kpi_source" in html
    assert "Win rate (${esc(kpiSource)})" in html or "Win rate (${esc(kpiSource)}" in html


def test_d6_paper_vs_spy_empty_book_does_not_surface_backtest_risk():
    """Fail-case: empty paper + backtest summary must not fill Paper vs SPY Sharpe/Max DD."""
    paper = {
        "empty_book": True,
        "source": "alpaca_equity_only",
        "risk": {"sharpe_ratio": None, "max_drawdown_pct": None},
    }
    kpis = compose_performance_kpis(
        paper,
        {"sharpe_ratio": 2.5, "max_drawdown_pct": 1.0, "win_rate_pct": 90.0},
    )
    assert kpis["kpi_source"] == "backtest"
    assert kpis["sharpe_ratio"] == 2.5
    card = paper_vs_spy_risk(paper)
    assert card["sharpe_ratio"] is None
    assert card["max_drawdown_pct"] is None


def test_d6_paper_vs_spy_card_template_reads_paper_risk_only():
    html = INDEX.read_text(encoding="utf-8")
    start = html.index("async function desk")
    end = html.index("async function orders")
    desk_fn = html[start:end]
    card_start = desk_fn.index("desk.vs_spy")
    card_end = desk_fn.index("Win rate (${esc(kpiSource)})")
    card = desk_fn[card_start:card_end]
    assert "perf.sharpe_ratio" not in card
    assert "perf.max_drawdown_pct" not in card
    assert "paper.risk" in desk_fn
    assert "paperEmpty" in desk_fn
    assert "paperSharpe" in card
    assert "paperMaxDd" in card


def test_d6_bakeoff_fallback_does_not_stamp_backtest_as_paper_snapshot():
    row = strategy_fallback_row(
        {
            "kpi_source": "backtest",
            "sharpe_ratio": 2.5,
            "total_return_pct": 88.0,
            "num_trades": 200,
            "paper": {"empty_book": True, "risk": {}, "pnl": {}, "trading": {}},
            "backtest": {
                "source": "stable_strategy_backtest",
                "sharpe_ratio": 2.5,
                "total_return_pct": 88.0,
                "num_trades": 200,
            },
        }
    )
    assert row is not None
    assert row["strategy"] != "paper snapshot"
    assert "paper" not in row["strategy"]
    assert row["strategy"] == "stable backtest"
    assert row["sharpe"] == 2.5


def test_d6_bakeoff_fallback_paper_uses_paper_fields_only():
    row = strategy_fallback_row(
        {
            "kpi_source": "paper",
            "sharpe_ratio": 0.2,
            "total_return_pct": 1.5,
            "paper": {
                "empty_book": False,
                "risk": {"sharpe_ratio": 0.2, "max_drawdown_pct": 5.0},
                "pnl": {"total_return_pct": 1.5},
                "trading": {"num_trades": 3},
                "benchmark": {"spy_return_pct": 2.0, "alpha_net_of_costs_pct": -0.5},
            },
            "backtest": {"source": "stable_strategy_backtest", "sharpe_ratio": 9.9, "total_return_pct": 77.0},
        }
    )
    assert row["strategy"] == "paper snapshot"
    assert row["sharpe"] == 0.2
    assert row["return_pct"] == 1.5
    assert row["sharpe"] != 9.9


def test_d6_strategies_fallback_template_does_not_stamp_toplevel_as_paper():
    html = INDEX.read_text(encoding="utf-8")
    start = html.index("async function strategies")
    end = html.index("async function calendar")
    fn = html[start:end]
    assert "paper snapshot" in fn
    assert "return_pct: p.total_return_pct" not in fn
    assert "sharpe: p.sharpe_ratio" not in fn
    assert "trades: p.num_trades" not in fn
    assert "kpi_source" in fn
    assert "stable backtest" in fn
    assert "bt.sharpe_ratio" in fn or "bt.sharpe" in fn


def test_d4_guide_api_does_not_teach_fresh_heartbeat_as_auto_on(honesty_api):
    res = honesty_api["client"].get("/api/guide")
    assert res.status_code == 200, res.text
    auto = next(step for step in res.json()["steps"] if step["id"] == "auto")
    en = auto["en"]["body"].lower()
    zh = auto["zh"]["body"]
    assert "heartbeat is fresh" not in en
    assert "心跳正常" not in zh
    assert "live pid" in en or "pid" in en
    assert "未驗證" in zh and "PID" in zh


def test_d4_user_guide_matches_live_pid_chip():
    text = USER_GUIDE.read_text(encoding="utf-8")
    assert "heartbeat is fresh" not in text
    assert "心跳正常" not in text
    assert "live pid" in text.lower()
    assert "未驗證心跳" in text
