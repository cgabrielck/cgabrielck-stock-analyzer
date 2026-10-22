"""Slice D D4–D7: fail-cases for dishonest Desk labels and silent fallback."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.desk_display import (
    BROKER_BOOK_DIR_LABEL,
    DUAL_BOOK_CUE,
    DUAL_BOOK_CUE_ZH,
    KPI_EMPTY_CUE,
    LAB_BOOK_DIR_LABEL,
    LOCAL_ORDER_CUE,
    UNPRICED_FILL_LABEL,
    broker_book_dir,
    compose_performance_kpis,
    desk_kpi_source_label,
    display_chips,
    dual_book_envelope,
    dual_books_split,
    is_priced_fill,
    journal_display_audit,
    journal_display_row,
    journal_status_class,
    journal_status_label,
    journal_summary_label,
    lab_book_dir,
    order_display_status,
    orders_book,
    paper_vs_spy_risk,
    strategy_fallback_row,
    worker_process_on,
)
from backend.trading.engine.order_manager import InMemoryOrderStore
from backend.trading.models import Order, OrderSide, OrderStatus, OrderType


ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "web" / "static" / "index.html"
APP_TSX = ROOT / "web" / "src" / "App.tsx"
USER_GUIDE = ROOT / "docs" / "CGAB_USER_GUIDE.md"
I18N = ROOT / "backend" / "i18n.py"
STREAMLIT_APP = ROOT / "backend" / "app.py"
DASHBOARD = ROOT / "backend" / "trading" / "ui" / "dashboard.py"
PORTFOLIO_MANAGER = ROOT / "backend" / "agents" / "portfolio_manager.py"
CONSTANTS = ROOT / "backend" / "utils" / "constants.py"


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
        {"running": True, "unverified": False, "process_evidence": "pid_alive", "alive": True, "pid": 2152},
        account_loaded=True,
    )
    assert chips["worker_running"] is True
    assert chips["auto_on"] is True
    assert chips["worker_label"] == "Worker running"
    assert chips["worker_tone"] == "on"


def test_d4_stale_alive_pid_is_not_on():
    """Fail-case: alive but stale heartbeat must not read as linked/ON."""
    status = {
        "running": True,
        "alive": True,
        "stale": True,
        "pid": 2152,
        "unverified": False,
        "process_evidence": "pid_alive",
    }
    assert worker_process_on(status) is False
    chips = display_chips(status, account_loaded=True)
    assert chips["auto_on"] is False
    assert chips["worker_running"] is False
    assert chips["worker_tone"] != "on"
    assert chips["worker_label"] == "Worker stale"
    assert "linked" not in chips["worker_label"].lower()
    assert chips["account_label"] == "Account loaded"


def test_d4_running_bit_without_pid_evidence_is_not_on():
    """Fail-case: a bare running=True / no pid must not light the chip."""
    status = {"running": True, "alive": False, "pid": None, "process_evidence": "none"}
    assert worker_process_on(status) is False
    chips = display_chips(status, account_loaded=True)
    assert chips["auto_on"] is False
    assert chips["worker_label"] == "Worker not running"
    assert "linked" not in chips["worker_label"].lower()
    assert chips["worker_tone"] != "on"


def test_d4_ui_does_not_treat_account_as_worker_linked():
    tsx = APP_TSX.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert 'Worker {account ? "linked"' not in tsx
    assert "Worker linked" not in tsx
    assert "accountChipLabel" in tsx
    assert "Worker unverified" in tsx
    assert "Worker stale" in tsx
    assert "Account loaded" in tsx
    assert "function workerProcessOn" in html
    assert "heartbeat_unverified" in html
    assert "worker.unverified" in html
    assert "worker.stale" in html
    assert "AUTO STALE" in html
    assert "display.auto_on" in html


# --- D5: GET /api/orders silent local-store fallback ------------------------


def test_d5_local_store_envelope_is_degraded():
    book = orders_book([{"id": "loc-1", "symbol": "AAPL"}], source="local_store", degraded=True, unverified=True)
    assert book["source"] == "local_store"
    assert book["degraded"] is True
    assert book["unverified"] is True
    assert book["verified"] is False
    assert book["live"] is False
    assert book["count"] == 1
    row = book["orders"][0]
    assert row["source"] == "local_store"
    assert row["degraded"] is True
    assert row["unverified"] is True
    assert row.get("verified") is not True
    assert row.get("live") is not True
    assert LOCAL_ORDER_CUE in row["display_status"]
    assert "broker" not in row["display_status"].lower()


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


def _patch_worker_heartbeat(monkeypatch, tmp_path, payload, *, pid=None, alive_pid=None):
    from backend.api import worker_ctl

    hb = tmp_path / "worker_heartbeat.json"
    pidf = tmp_path / "worker.pid"
    hb.write_text(__import__("json").dumps(payload), encoding="utf-8")
    if pid is not None:
        pidf.write_text(str(pid), encoding="utf-8")
    monkeypatch.setattr(worker_ctl, "HEARTBEAT_PATH", hb)
    monkeypatch.setattr(worker_ctl, "PID_PATH", pidf)
    if alive_pid is None:
        monkeypatch.setattr(worker_ctl, "pid_alive", lambda _pid: False)
    else:
        monkeypatch.setattr(worker_ctl, "pid_alive", lambda p: p == alive_pid)
    return worker_ctl


def test_d4_worker_status_stale_pid_is_not_auto_on(honesty_api, monkeypatch, tmp_path):
    """Fail-case: GET /api/worker/status with stale heartbeat must not light AUTO ON."""
    from datetime import datetime, timedelta, timezone

    from backend.api import worker_ctl

    old = (datetime.now(timezone.utc) - timedelta(seconds=worker_ctl.STALE_AFTER_SEC + 45)).isoformat()
    _patch_worker_heartbeat(
        monkeypatch,
        tmp_path,
        {"running": True, "pid": 2152, "ts": old, "paper": True, "strategy": "breakout"},
        pid=2152,
        alive_pid=2152,
    )
    res = honesty_api["client"].get("/api/worker/status")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["running"] is True
    assert body["stale"] is True
    assert body["pid"] == 2152
    assert body["display"]["auto_on"] is False
    assert body["display"]["worker_running"] is False
    assert body["display"]["worker_tone"] != "on"
    assert body["display"]["worker_label"] == "Worker stale"
    assert "linked" not in body["display"]["worker_label"].lower()
    assert body.get("verified") is not True
    assert body.get("live") is not True


def test_d4_worker_status_no_pid_is_not_auto_on(honesty_api, monkeypatch, tmp_path):
    """Fail-case: fresh heartbeat claiming running without a pid is not linked/ON."""
    from datetime import datetime, timezone

    _patch_worker_heartbeat(
        monkeypatch,
        tmp_path,
        {
            "running": True,
            "ts": datetime.now(timezone.utc).isoformat(),
            "paper": True,
            "strategy": "breakout",
        },
    )
    res = honesty_api["client"].get("/api/worker/status")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["running"] is False
    assert body["pid"] is None
    assert body["unverified"] is True
    assert body["display"]["auto_on"] is False
    assert body["display"]["worker_label"] == "Worker unverified"
    assert "linked" not in body["display"]["worker_label"].lower()


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
    row = next(r for r in body["orders"] if r.get("id") == "loc-fallback-1" or r.get("symbol") == "MSFT")
    assert row["source"] == "local_store"
    assert row["degraded"] is True
    assert row["unverified"] is True
    assert row.get("verified") is not True
    assert row.get("live") is not True
    assert LOCAL_ORDER_CUE in (row.get("display_status") or "")
    assert "broker" not in (row.get("display_status") or "").lower()


def test_d5_broker_open_book_keeps_alpaca_done_for_day():
    """Fail-case: broker OPEN rows must not be re-filtered by the local allowlist."""
    book = orders_book(
        [
            {"id": "brk-dfd", "symbol": "AAPL", "status": "done_for_day"},
            {"id": "brk-new", "symbol": "MSFT", "status": "new"},
            {"id": "brk-fill", "symbol": "NVDA", "status": "filled", "filled_avg_price": 12.5},
        ],
        source="broker",
        degraded=False,
        unverified=False,
        status="open",
    )
    assert book["source"] == "broker"
    assert book["degraded"] is False
    ids = [row["id"] for row in book["orders"]]
    assert "brk-dfd" in ids
    assert "brk-new" in ids
    assert "brk-fill" in ids
    dfd = next(row for row in book["orders"] if row["id"] == "brk-dfd")
    assert dfd["status"] == "done_for_day"
    assert dfd["degraded"] is False
    assert LOCAL_ORDER_CUE not in (dfd.get("display_status") or "")


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
    assert body["orders"][0]["source"] == "broker"
    assert body["orders"][0]["degraded"] is False
    assert LOCAL_ORDER_CUE not in (body["orders"][0].get("display_status") or "")


def _broker_order(oid, symbol, status, *, filled_avg_price=None):
    row = MagicMock()
    row.id = oid
    row.symbol = symbol
    row.side = "buy"
    row.type = "limit"
    row.qty = 1
    row.status = status
    row.limit_price = 10.0
    row.filled_avg_price = filled_avg_price
    row.submitted_at = None
    return row


def test_d5_broker_open_keeps_done_for_day_while_fallback_drops_priced_fill(honesty_api):
    """Fail-case: clean broker OPEN keeps done_for_day; local fallback still hides priced fills."""
    honesty_api["broker"].api.get_orders.return_value = [
        _broker_order("brk-dfd", "AAPL", "done_for_day"),
        _broker_order("brk-new", "MSFT", "new"),
        _broker_order("brk-fill", "NVDA", "filled", filled_avg_price=12.5),
    ]
    res = honesty_api["client"].get("/api/orders?status=open")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "broker"
    assert body["degraded"] is False
    assert body["unverified"] is False
    ids = [row.get("id") for row in body["orders"]]
    assert "brk-dfd" in ids
    assert "brk-new" in ids
    dfd = next(row for row in body["orders"] if row["id"] == "brk-dfd")
    assert dfd["status"] == "done_for_day"
    assert dfd["source"] == "broker"
    assert dfd["degraded"] is False
    assert LOCAL_ORDER_CUE not in (dfd.get("display_status") or "")
    assert body.get("verified") is not True
    assert body.get("live") is not True

    honesty_api["broker"].api.get_orders.side_effect = RuntimeError("alpaca unavailable")
    honesty_api["store"].save_order(
        Order(
            id="loc-filled",
            symbol="NVDA",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
            status=OrderStatus.FILLED,
            filled_avg_price=12.5,
            idempotency_key="idem-filled-dfd",
        )
    )
    honesty_api["store"].save_order(
        Order(
            id="loc-open",
            symbol="MSFT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2,
            status=OrderStatus.SUBMITTED,
            idempotency_key="idem-open-dfd",
        )
    )
    fallback = honesty_api["client"].get("/api/orders?status=open")
    assert fallback.status_code == 200, fallback.text
    fb = fallback.json()
    assert fb["source"] == "local_store"
    assert fb["degraded"] is True
    assert fb["unverified"] is True
    fb_ids = [row.get("id") for row in fb["orders"]]
    assert "loc-filled" not in fb_ids
    assert "loc-open" in fb_ids
    open_row = next(row for row in fb["orders"] if row["id"] == "loc-open")
    assert LOCAL_ORDER_CUE in open_row["display_status"]


def test_d5_open_fallback_does_not_present_filled_local_as_broker(honesty_api):
    """Fail-case: broker down + status=open must not list a priced local fill as a live broker row."""
    honesty_api["broker"].api.get_orders.side_effect = RuntimeError("alpaca unavailable")
    honesty_api["store"].save_order(
        Order(
            id="loc-filled",
            symbol="NVDA",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
            status=OrderStatus.FILLED,
            filled_avg_price=12.5,
            idempotency_key="idem-filled",
        )
    )
    honesty_api["store"].save_order(
        Order(
            id="loc-open",
            symbol="MSFT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=2,
            status=OrderStatus.SUBMITTED,
            idempotency_key="idem-open",
        )
    )
    res = honesty_api["client"].get("/api/orders?status=open")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "local_store"
    assert body["degraded"] is True
    ids = [row.get("id") for row in body["orders"]]
    assert "loc-filled" not in ids
    assert "loc-open" in ids
    open_row = next(row for row in body["orders"] if row["id"] == "loc-open")
    assert open_row["source"] == "local_store"
    assert open_row["unverified"] is True
    assert LOCAL_ORDER_CUE in open_row["display_status"]
    assert order_display_status(open_row) == open_row["display_status"]


def test_d5_ui_surfaces_degraded_orders_source():
    html = INDEX.read_text(encoding="utf-8")
    tsx = APP_TSX.read_text(encoding="utf-8")
    assert "orders.degraded" in html
    assert "local_store" in html
    assert "not a clean broker book" in html
    assert "ordersBookDirty" in tsx
    assert "not a clean broker book" in tsx
    assert "local/unverified" in tsx
    assert "display_status" in tsx
    assert "display_status" in html


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
    assert out["empty_book"] is False
    assert out["kpi_label"] == "paper"
    assert out["win_rate_pct"] == 40.0
    assert out["win_rate_pct"] != summary["win_rate_pct"]
    assert out["total_return_pct"] == 3.2
    assert out["verified"] is False
    assert out["live"] is False
    assert desk_kpi_source_label(kpi_source="paper", empty_book=False) == "paper"


def test_d6_empty_paper_does_not_copy_backtest_into_kpis():
    """Fail-case: empty_book must not silently show stable backtest numbers."""
    paper = {"empty_book": True, "trading": {"win_rate_pct": None, "num_trades": 0}}
    summary = {"win_rate_pct": 55.0, "num_trades": 12, "total_return_pct": 10.0}
    out = compose_performance_kpis(paper, summary)
    assert out["kpi_source"] != "backtest"
    assert out["kpi_source"] == "none"
    assert out["empty_book"] is True
    assert out["kpi_label"] == "empty"
    assert out["win_rate_pct"] is None
    assert out["win_rate_pct"] != summary["win_rate_pct"]
    assert out["num_trades"] is None
    assert out["kpi_cue"] == KPI_EMPTY_CUE
    assert desk_kpi_source_label(kpi_source="none", empty_book=True) == "empty"
    assert desk_kpi_source_label(kpi_source="backtest", empty_book=True) == "empty"


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
    assert body["empty_book"] is False
    assert body["kpi_label"] == "paper"
    assert body["win_rate_pct"] == 33.0
    assert body["win_rate_pct"] != 99.0
    assert body["backtest"]["win_rate_pct"] == 99.0
    assert body.get("verified") is not True
    assert body.get("live") is not True


def test_d6_performance_api_empty_book_does_not_show_backtest_kpis(honesty_api, monkeypatch, tmp_path):
    """Fail-case: empty paper + stable summary must not fill Desk KPIs with backtest."""
    from backend.api import app as app_mod
    from backend.api import paper_performance

    paper = {
        "available": True,
        "empty_book": True,
        "source": "alpaca_equity_only",
        "trading": {"win_rate_pct": None, "profit_factor": None, "num_trades": 0},
        "risk": {"sharpe_ratio": None, "max_drawdown_pct": None},
        "pnl": {"total_return_pct": None},
        "benchmark": {"spy_return_pct": None, "alpha_net_of_costs_pct": None, "beats_spy_net": None},
        "disclaimer": "paper only",
    }
    monkeypatch.setattr(paper_performance, "load_cached_paper_report", lambda: None)
    monkeypatch.setattr(paper_performance, "build_paper_report", lambda persist=True: paper)
    monkeypatch.setattr(app_mod, "DATA_DIR", tmp_path)
    (tmp_path / "strategy_backtest_stable_summary.json").write_text(
        '{"win_rate_pct": 99.0, "total_return_pct": 77.0, "num_trades": 500, "sharpe_ratio": 2.5}',
        encoding="utf-8",
    )
    res = honesty_api["client"].get("/api/performance")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["empty_book"] is True
    assert body["kpi_source"] != "backtest"
    assert body["kpi_label"] == "empty"
    assert body["win_rate_pct"] is None
    assert body["win_rate_pct"] != 99.0
    assert body["sharpe_ratio"] is None
    assert body["sharpe_ratio"] != 2.5
    assert body["kpi_cue"] == KPI_EMPTY_CUE
    assert body["backtest"]["win_rate_pct"] == 99.0
    assert body["backtest"]["sharpe_ratio"] == 2.5
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
    assert "Empty paper book — not backtest" in tsx
    assert "desk.kpi.empty" in html
    assert 'kpiSource==="empty"' in html
    assert 'empty_book' in tsx


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
    assert kpis["kpi_source"] != "backtest"
    assert kpis["empty_book"] is True
    assert kpis["sharpe_ratio"] is None
    assert kpis["sharpe_ratio"] != 2.5
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


# --- plan-12e: live UI copy index.html vs App.tsx ---------------------------

HOLDINGS_HINT = "Alpaca Paper real book (not a model portfolio)"
EMPTY_POSITIONS = "No positions yet — paper auto only fills when a signal passes every gate."
OPEN_ORDERS_HINT = "new / accepted = sent to the broker, not filled yet. The limit must trade before shares hit the book."
UNPRICED_FILL = "unpriced — not a fill"
PAPER_GATE = "Do not set APCA_PAPER=false until the paper runbook is signed."
JOURNAL_LEAD = (
    "Orders, risk rejects, and operator notes from the local ledger. "
    "Empty means nothing was recorded — not a live fill. "
    "Fills appear only with a recorded fill price. Streamlit journal is lab/legacy."
)
ALERTS_BODY = (
    "New rules attach to saved research plans. Telegram worker stays the delivery path. "
    "Not a live fill feed."
)
AIMODE_LEAD = (
    "Only place to tune the agent and start paper auto trading. "
    "LLM cannot bypass RiskEngine / kill / mandate."
)
MANDATE_GAP = "Desk and paper worker fail-closed until mandate.json loads."


def test_plan12e_no_unverified_live_or_filled_claims():
    html = INDEX.read_text(encoding="utf-8")
    tsx = APP_TSX.read_text(encoding="utf-8")
    assert "live paper book" not in html.lower()
    assert "live paper book" not in tsx.lower()
    assert "Worker linked" not in tsx
    assert "delivery live" not in tsx.lower()
    assert "Details also live in" not in html
    assert "worker still fail-open" not in html.lower()
    assert "paper worker still fail-open" not in html.lower()
    assert '"verified": true' not in tsx.lower()
    assert "verified: true" not in tsx.lower()


def test_plan12e_copy_aligned_between_product_html_and_app_tsx():
    html = INDEX.read_text(encoding="utf-8")
    tsx = APP_TSX.read_text(encoding="utf-8")
    for phrase in (
        HOLDINGS_HINT,
        DUAL_BOOK_CUE,
        EMPTY_POSITIONS,
        OPEN_ORDERS_HINT,
        UNPRICED_FILL,
        PAPER_GATE,
        JOURNAL_LEAD,
        ALERTS_BODY,
        AIMODE_LEAD,
        MANDATE_GAP,
        "positiveFillPrice",
        "orderStatusLabel",
        "journalStatusLabel",
        "journalSummaryLabel",
    ):
        assert phrase in html, phrase
        assert phrase in tsx, phrase


def test_plan12e_unpriced_fill_is_not_shown_as_filled_in_ui():
    html = INDEX.read_text(encoding="utf-8")
    tsx = APP_TSX.read_text(encoding="utf-8")
    html_priced = html[html.index("function isPricedFill") : html.index("function orderStatusLabel")]
    tsx_priced = tsx[tsx.index("function isPricedFill") : tsx.index("function orderStatusLabel")]
    assert "fills.length" not in html_priced
    assert "fills.length" not in tsx_priced
    assert "positiveFillPrice" in html_priced
    assert "positiveFillPrice" in tsx_priced
    assert "journalSummaryLabel" in html
    assert "journalSummaryLabel" in tsx


def _unpriced_journal_rows():
    return [
        {
            "schema_version": "trade_log.v0",
            "log_id": "tl-zero-price",
            "symbol": "AAPL",
            "ts": "2026-10-22T12:00:00+00:00",
            "status": "filled",
            "kind": "fill",
            "side": "buy",
            "qty": 1,
            "fills": [{"price": 0, "qty": 1}],
            "user_visible_summary": "filled buy 1 AAPL",
        },
        {
            "schema_version": "trade_log.v0",
            "log_id": "tl-qty-only",
            "symbol": "MSFT",
            "ts": "2026-10-22T12:01:00+00:00",
            "status": "filled",
            "kind": "fill",
            "side": "buy",
            "qty": 1,
            "fills": [{"qty": 1}],
            "user_visible_summary": "filled buy 1 MSFT",
        },
        {
            "schema_version": "trade_log.v0",
            "log_id": "tl-limit-only",
            "symbol": "NVDA",
            "ts": "2026-10-22T12:02:00+00:00",
            "status": "filled",
            "kind": "fill",
            "side": "buy",
            "qty": 2,
            "fills": [{"limit_price": 100, "qty": 2}],
            "user_visible_summary": "filled buy 2 NVDA",
        },
        {
            "schema_version": "trade_log.v0",
            "log_id": "tl-empty-fills",
            "symbol": "GOOG",
            "ts": "2026-10-22T12:03:00+00:00",
            "status": "filled",
            "kind": "fill",
            "side": "buy",
            "qty": 1,
            "fills": [],
            "user_visible_summary": "filled buy 1 GOOG",
        },
        {
            "schema_version": "trade_log.v0",
            "log_id": "tl-top-zero",
            "symbol": "META",
            "ts": "2026-10-22T12:04:00+00:00",
            "status": "filled",
            "kind": "fill",
            "side": "buy",
            "qty": 1,
            "fills": [],
            "filled_avg_price": 0,
            "user_visible_summary": "filled buy 1 META",
        },
    ]


def test_plan12e_unpriced_fills_array_is_not_journal_filled():
    """Fail-case: zero / missing / limit-only fills[] must not render as filled."""
    for raw in _unpriced_journal_rows():
        assert is_priced_fill(raw) is False
        shown = journal_display_row(raw)
        assert journal_status_label(raw) == UNPRICED_FILL_LABEL
        assert shown["display_status"] == UNPRICED_FILL_LABEL
        assert shown["display_status"] != "filled"
        assert journal_status_class(raw) == "submitted"
        assert shown["display_status_class"] == "submitted"
        assert shown["display_status_class"] != "filled"
        assert "filled" not in shown["user_visible_summary"].lower()
        assert UNPRICED_FILL_LABEL in shown["user_visible_summary"]
        assert shown.get("kind") != "fill"
        assert shown.get("status") != "filled"
        assert shown.get("status") != "partially_filled"
        assert shown["status"] == UNPRICED_FILL_LABEL
        assert journal_summary_label(raw) == UNPRICED_FILL_LABEL
        dumped = __import__("json").dumps(shown).lower()
        assert '"status": "filled"' not in dumped
        assert '"status":"filled"' not in dumped


def test_plan12e_mixed_unpriced_filled_buy_summary_is_demoted():
    raw = {
        "status": "filled",
        "kind": "fill",
        "fills": [{"price": 0, "qty": 1}],
        "user_visible_summary": "unpriced but filled buy 1 ORCL",
    }
    shown = journal_display_row(raw)
    assert shown["status"] == UNPRICED_FILL_LABEL
    assert shown["user_visible_summary"] == UNPRICED_FILL_LABEL
    assert "filled buy" not in shown["user_visible_summary"].lower()
    assert journal_summary_label(raw) == UNPRICED_FILL_LABEL


def test_plan12e_priced_fills_array_still_shows_filled():
    raw = {
        "status": "filled",
        "kind": "fill",
        "fills": [{"price": 12.5, "qty": 1}],
        "user_visible_summary": "filled buy 1 AAPL",
    }
    shown = journal_display_row(raw)
    assert is_priced_fill(raw) is True
    assert shown["display_status"] == "filled"
    assert shown["display_status_class"] == "filled"
    assert shown["user_visible_summary"] == "filled buy 1 AAPL"
    assert shown.get("kind") == "fill"


def _assert_journal_row_not_priced_filled(row: dict) -> None:
    import json

    st = str(row.get("status") or "")
    assert st.lower() != "filled"
    assert st.lower() != "partially_filled"
    assert "filled" not in st.lower()
    assert st == UNPRICED_FILL_LABEL
    assert row["display_status"] == UNPRICED_FILL_LABEL
    assert row["display_status"] != "filled"
    assert row["display_status_class"] == "submitted"
    assert row["display_status_class"] != "filled"
    assert "filled" not in str(row.get("user_visible_summary") or "").lower()
    assert UNPRICED_FILL_LABEL in str(row.get("user_visible_summary") or "")
    assert row.get("kind") != "fill"
    assert row.get("verified") is not True
    assert (row.get("provenance") or {}).get("live") is not True
    dumped = json.dumps(row).lower()
    assert '"status": "filled"' not in dumped
    assert '"status":"filled"' not in dumped
    assert '"kind": "fill"' not in dumped


def test_plan12e_journal_api_unpriced_fills_not_shown_filled(honesty_api, tmp_path, monkeypatch):
    import json

    from backend.utils.provenance import journal_store

    path = tmp_path / "trade_journal.json"
    path.write_text(json.dumps({"schema_version": "trade_log.v0", "entries": _unpriced_journal_rows()}), encoding="utf-8")
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", path)
    before = path.read_bytes()
    res = honesty_api["client"].get("/api/journal")
    assert res.status_code == 200, res.text
    body = res.json()
    by_id = {row.get("log_id"): row for row in body["entries"]}
    for raw in _unpriced_journal_rows():
        row = by_id.get(raw["log_id"])
        assert row is not None, raw["log_id"]
        _assert_journal_row_not_priced_filled(row)
    dumped = json.dumps(body).lower()
    assert '"status": "filled"' not in dumped
    assert '"status":"filled"' not in dumped
    assert '"kind": "fill"' not in dumped
    assert path.read_bytes() == before


def test_plan12e_journal_entry_api_unpriced_fills_not_shown_filled(honesty_api, tmp_path, monkeypatch):
    """Fail-case: click-through GET /api/journal/entry/{log_id} must use the list overlay."""
    import json

    from backend.utils.provenance import journal_store

    path = tmp_path / "trade_journal.json"
    path.write_text(json.dumps({"schema_version": "trade_log.v0", "entries": _unpriced_journal_rows()}), encoding="utf-8")
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", path)
    before = path.read_bytes()
    for raw in _unpriced_journal_rows():
        res = honesty_api["client"].get(f"/api/journal/entry/{raw['log_id']}")
        assert res.status_code == 200, res.text
        payload = res.json()
        entry = payload.get("entry") or {}
        assert entry.get("log_id") == raw["log_id"]
        _assert_journal_row_not_priced_filled(entry)
        dumped = json.dumps(payload).lower()
        assert '"kind": "fill"' not in dumped
        assert '"status": "filled"' not in dumped
        assert '"status":"filled"' not in dumped
        assert "filled buy" not in dumped
        assert payload.get("verified") is not True
    assert path.read_bytes() == before


def _audit_dir_bytes(audit_dir: Path) -> bytes:
    parts = []
    for path in sorted(audit_dir.glob("audit_*.jsonl")):
        parts.append(path.name.encode("utf-8") + b"\n" + path.read_bytes())
    return b"".join(parts)


def _assert_no_dishonest_fill_status(blob) -> None:
    import json

    dumped = blob.lower() if isinstance(blob, str) else json.dumps(blob).lower()
    assert '"status": "filled"' not in dumped
    assert '"status":"filled"' not in dumped
    assert '"status": "partially_filled"' not in dumped
    assert '"status":"partially_filled"' not in dumped


def test_plan12e_journal_display_audit_demotes_unpriced_payload_status():
    entry = journal_display_row(
        {
            "status": "filled",
            "kind": "fill",
            "fills": [{"price": 0, "qty": 1}],
            "user_visible_summary": "filled buy 1 AAPL",
        }
    )
    raw_audit = {
        "event_id": "aud-unpriced",
        "event_type": "order_submission",
        "status": "filled",
        "limit_price": 100,
        "payload": {"status": "filled", "limit_price": 100, "order_id": "ord-1"},
    }
    shown = journal_display_audit(raw_audit, entry=entry)
    assert shown is not None
    assert shown["status"] == UNPRICED_FILL_LABEL
    assert shown["payload"]["status"] == UNPRICED_FILL_LABEL
    _assert_no_dishonest_fill_status(shown)
    assert raw_audit["status"] == "filled"
    assert raw_audit["payload"]["status"] == "filled"


def test_plan12e_journal_display_audit_demotes_submitted_and_proposed_entries():
    """from_order / normalize often leave submitted or proposed, not unpriced."""
    filled_audit = {
        "event_id": "aud-sub",
        "status": "filled",
        "limit_price": 100,
        "payload": {"status": "filled", "limit_price": 100},
    }
    submitted = journal_display_audit(
        filled_audit,
        entry={"status": "submitted", "kind": "order", "fills": [], "limit_price": 100},
    )
    assert submitted is not None
    assert submitted["status"] == UNPRICED_FILL_LABEL
    assert submitted["payload"]["status"] == UNPRICED_FILL_LABEL
    _assert_no_dishonest_fill_status(submitted)

    partial_audit = {
        "event_id": "aud-prop",
        "status": "partially_filled",
        "limit_price": 50,
        "payload": {"status": "partially_filled", "limit_price": 50},
    }
    proposed = journal_display_audit(
        partial_audit,
        entry={"status": "proposed", "kind": "note", "fills": []},
    )
    assert proposed is not None
    assert proposed["status"] == UNPRICED_FILL_LABEL
    assert proposed["payload"]["status"] == UNPRICED_FILL_LABEL
    _assert_no_dishonest_fill_status(proposed)
    assert filled_audit["status"] == "filled"
    assert partial_audit["status"] == "partially_filled"


def test_plan12e_journal_entry_unpriced_linked_audit_not_shown_filled(
    honesty_api, tmp_path, monkeypatch
):
    """Fail-case: Entry+audit must not leak audit.status=filled for an unpriced fill."""
    import json

    from backend.trading.safety import audit
    from backend.utils.provenance import journal_store

    journal = tmp_path / "trade_journal.json"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)

    event_id = audit.log_order_submission(
        symbol="AAPL",
        side="buy",
        quantity=1.0,
        limit_price=100.0,
        order_id="ord-unpriced",
        status="filled",
    )
    assert event_id
    row = {
        "schema_version": "trade_log.v0",
        "log_id": "tl-unpriced-audit",
        "symbol": "AAPL",
        "ts": "2026-10-22T12:00:00+00:00",
        "status": "filled",
        "kind": "fill",
        "side": "buy",
        "qty": 1,
        "fills": [{"price": 0, "qty": 1}],
        "user_visible_summary": "filled buy 1 AAPL",
        "risk_decision_id": event_id,
    }
    journal.write_text(
        json.dumps({"schema_version": "trade_log.v0", "entries": [row]}),
        encoding="utf-8",
    )
    before_journal = journal.read_bytes()
    before_audit = _audit_dir_bytes(audit_dir)

    res = honesty_api["client"].get("/api/journal/entry/tl-unpriced-audit")
    assert res.status_code == 200, res.text
    _assert_no_dishonest_fill_status(res.text)
    payload = res.json()
    entry = payload.get("entry") or {}
    linked = payload.get("audit") or {}
    assert entry.get("log_id") == "tl-unpriced-audit"
    _assert_journal_row_not_priced_filled(entry)
    assert linked.get("event_id") == event_id
    assert linked.get("status") == UNPRICED_FILL_LABEL
    assert (linked.get("payload") or {}).get("status") == UNPRICED_FILL_LABEL
    _assert_no_dishonest_fill_status(payload)
    assert "filled buy" not in json.dumps(payload).lower()
    assert journal.read_bytes() == before_journal
    assert _audit_dir_bytes(audit_dir) == before_audit


def test_plan12e_journal_entry_priced_linked_audit_keeps_filled(
    honesty_api, tmp_path, monkeypatch
):
    """Control: a priced fill may still show status=filled on entry and audit."""
    import json

    from backend.trading.safety import audit
    from backend.utils.provenance import journal_store

    journal = tmp_path / "trade_journal.json"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)

    event_id = audit.log_event(
        "order_submission",
        {
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1.0,
            "limit_price": 100.0,
            "order_id": "ord-priced",
            "status": "filled",
            "filled_avg_price": 12.5,
        },
        actor="worker",
        kind="order_submit",
    )
    assert event_id
    row = {
        "schema_version": "trade_log.v0",
        "log_id": "tl-priced-audit",
        "symbol": "AAPL",
        "ts": "2026-10-22T12:05:00+00:00",
        "status": "filled",
        "kind": "fill",
        "side": "buy",
        "qty": 1,
        "fills": [{"price": 12.5, "qty": 1}],
        "user_visible_summary": "filled buy 1 AAPL",
        "risk_decision_id": event_id,
    }
    journal.write_text(
        json.dumps({"schema_version": "trade_log.v0", "entries": [row]}),
        encoding="utf-8",
    )
    before_journal = journal.read_bytes()
    before_audit = _audit_dir_bytes(audit_dir)

    res = honesty_api["client"].get("/api/journal/entry/tl-priced-audit")
    assert res.status_code == 200, res.text
    payload = res.json()
    entry = payload.get("entry") or {}
    linked = payload.get("audit") or {}
    assert entry.get("status") == "filled"
    assert entry.get("kind") == "fill"
    assert entry.get("user_visible_summary") == "filled buy 1 AAPL"
    assert linked.get("event_id") == event_id
    assert linked.get("status") == "filled"
    assert (linked.get("payload") or {}).get("status") == "filled"
    dumped = json.dumps(payload).lower()
    assert '"status": "filled"' in dumped
    assert journal.read_bytes() == before_journal
    assert _audit_dir_bytes(audit_dir) == before_audit


def test_plan12e_journal_entry_from_order_unpriced_audit_not_shown_filled(
    honesty_api, tmp_path, monkeypatch
):
    """Product path: from_order(FILLED, no fill price) stays submitted; audit must not say filled."""
    from backend.trading.safety import audit
    from backend.utils.provenance import journal_store
    from backend.utils.provenance.journal_store import record_from_order

    journal = tmp_path / "trade_journal.json"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)

    event_id = audit.log_order_submission(
        symbol="AAPL",
        side="buy",
        quantity=1.0,
        limit_price=100.0,
        order_id="ord-from-order-unpriced",
        status="filled",
    )
    assert event_id
    stored = record_from_order(
        Order(
            id="ord-from-order-unpriced",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=1,
            limit_price=100.0,
            status=OrderStatus.FILLED,
            filled_avg_price=None,
            idempotency_key="idem-from-order-unpriced",
        ),
        audit_id=event_id,
    )
    assert stored is not None
    assert stored["status"] == "submitted"
    assert stored["fills"] == []
    log_id = stored["log_id"]
    before_journal = journal.read_bytes()
    before_audit = _audit_dir_bytes(audit_dir)

    res = honesty_api["client"].get(f"/api/journal/entry/{log_id}")
    assert res.status_code == 200, res.text
    _assert_no_dishonest_fill_status(res.text)
    payload = res.json()
    entry = payload.get("entry") or {}
    linked = payload.get("audit") or {}
    assert entry.get("log_id") == log_id
    assert entry.get("status") == "submitted"
    assert is_priced_fill(entry) is False
    assert linked.get("event_id") == event_id
    assert linked.get("status") == UNPRICED_FILL_LABEL
    assert (linked.get("payload") or {}).get("status") == UNPRICED_FILL_LABEL
    _assert_no_dishonest_fill_status(payload)
    assert journal.read_bytes() == before_journal
    assert _audit_dir_bytes(audit_dir) == before_audit


def test_plan12e_journal_entry_audit_only_unpriced_not_shown_filled(
    honesty_api, tmp_path, monkeypatch
):
    """Product path: audit-jsonl-only row is submitted; Entry+audit must not say filled."""
    from backend.trading.safety import audit
    from backend.utils.provenance import journal_store

    journal = tmp_path / "trade_journal.json"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)

    event_id = audit.log_order_submission(
        symbol="NVDA",
        side="buy",
        quantity=1.0,
        limit_price=200.0,
        order_id="ord-audit-only",
        status="filled",
    )
    assert event_id
    before_audit = _audit_dir_bytes(audit_dir)
    assert not journal.exists()

    listed = honesty_api["client"].get("/api/journal")
    assert listed.status_code == 200, listed.text
    row = next(
        e for e in listed.json()["entries"] if e.get("risk_decision_id") == event_id
    )
    assert row.get("status") == "submitted"
    assert is_priced_fill(row) is False
    log_id = row["log_id"]
    assert not journal.exists()

    res = honesty_api["client"].get(f"/api/journal/entry/{log_id}")
    assert res.status_code == 200, res.text
    _assert_no_dishonest_fill_status(res.text)
    payload = res.json()
    entry = payload.get("entry") or {}
    linked = payload.get("audit") or {}
    assert entry.get("log_id") == log_id
    assert entry.get("status") == "submitted"
    assert linked.get("event_id") == event_id
    assert linked.get("status") == UNPRICED_FILL_LABEL
    assert (linked.get("payload") or {}).get("status") == UNPRICED_FILL_LABEL
    _assert_no_dishonest_fill_status(payload)
    assert not journal.exists()
    assert _audit_dir_bytes(audit_dir) == before_audit


def test_plan12e_journal_entry_stored_partially_filled_audit_not_shown_filled(
    honesty_api, tmp_path, monkeypatch
):
    """Stored partially_filled normalizes to proposed; linked audit must not stay partially_filled."""
    import json

    from backend.trading.safety import audit
    from backend.utils.provenance import journal_store

    journal = tmp_path / "trade_journal.json"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)

    event_id = audit.log_event(
        "order_submission",
        {
            "symbol": "IBM",
            "side": "buy",
            "quantity": 1.0,
            "limit_price": 100.0,
            "order_id": "ord-partial",
            "status": "partially_filled",
        },
        actor="worker",
        kind="order_submit",
    )
    assert event_id
    row = {
        "schema_version": "trade_log.v0",
        "log_id": "tl-partial-stored",
        "symbol": "IBM",
        "ts": "2026-10-22T12:06:00+00:00",
        "status": "partially_filled",
        "kind": "fill",
        "side": "buy",
        "qty": 1,
        "fills": [{"price": 0, "qty": 1}],
        "user_visible_summary": "filled buy 1 IBM",
        "risk_decision_id": event_id,
    }
    journal.write_text(
        json.dumps({"schema_version": "trade_log.v0", "entries": [row]}),
        encoding="utf-8",
    )
    before_journal = journal.read_bytes()
    before_audit = _audit_dir_bytes(audit_dir)

    res = honesty_api["client"].get("/api/journal/entry/tl-partial-stored")
    assert res.status_code == 200, res.text
    _assert_no_dishonest_fill_status(res.text)
    payload = res.json()
    entry = payload.get("entry") or {}
    linked = payload.get("audit") or {}
    assert entry.get("log_id") == "tl-partial-stored"
    assert entry.get("status") == "proposed"
    assert is_priced_fill(entry) is False
    assert linked.get("event_id") == event_id
    assert linked.get("status") == UNPRICED_FILL_LABEL
    assert (linked.get("payload") or {}).get("status") == UNPRICED_FILL_LABEL
    _assert_no_dishonest_fill_status(payload)
    assert journal.read_bytes() == before_journal
    assert _audit_dir_bytes(audit_dir) == before_audit


def test_plan12e_guide_api_desk_is_paper_book_not_live(honesty_api):
    res = honesty_api["client"].get("/api/guide")
    assert res.status_code == 200, res.text
    desk = next(step for step in res.json()["steps"] if step["id"] == "desk")
    title = desk["en"]["title"].lower()
    assert "live paper" not in title
    assert "paper book" in title
    assert desk["en"]["title"] == "Desk is the paper book"


def test_plan12e_journal_disclaimer_matches_ui_lead(honesty_api):
    res = honesty_api["client"].get("/api/journal")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["disclaimer"] == JOURNAL_LEAD
    assert "not a live fill" in body["disclaimer"].lower()
    assert "recorded fill price" in body["disclaimer"].lower()
    assert body.get("verified") is not True
    assert body.get("live") is not True


# --- D7: dual-book honesty (Kelly/lab data/ ≠ Desk broker backend/data/) ---


def test_d7_source_dirs_remain_split():
    """D7-DESIGN still OPEN: two writers, two directories."""
    from backend.utils.constants import DATA_DIR as broker

    pm = PORTFOLIO_MANAGER.read_text(encoding="utf-8")
    const = CONSTANTS.read_text(encoding="utf-8")
    assert '"..", "..", "data"' in pm
    assert 'os.path.dirname(os.path.dirname(__file__)), "data"' in const
    assert "do not silently merge" in pm.lower()
    # Same formula as portfolio_manager.DATA_DIR without importing yfinance.
    lab_from_pm = (PORTFOLIO_MANAGER.resolve().parent.parent.parent / "data").resolve()
    assert lab_book_dir() == lab_from_pm
    assert broker_book_dir() == Path(broker).resolve()
    assert lab_book_dir() != broker_book_dir()
    assert dual_books_split() is True


def test_d7_holdings_hint_is_not_the_dual_book_banner():
    """D14 PASS copy must not be treated as D7-HONESTY."""
    assert "backend/data/" not in HOLDINGS_HINT
    assert HOLDINGS_HINT != DUAL_BOOK_CUE
    assert "two directories" not in HOLDINGS_HINT.lower()
    assert "not the same book" not in HOLDINGS_HINT.lower()


def test_d7_honesty_surface_cannot_disappear_while_dirs_split():
    """Fail-case: removing the dual-book warning while dirs remain split."""
    assert dual_books_split() is True
    html = INDEX.read_text(encoding="utf-8")
    tsx = APP_TSX.read_text(encoding="utf-8")
    i18n = I18N.read_text(encoding="utf-8")
    guide = USER_GUIDE.read_text(encoding="utf-8")
    streamlit = STREAMLIT_APP.read_text(encoding="utf-8")
    dash = DASHBOARD.read_text(encoding="utf-8")
    for blob, label in (
        (html, "index.html"),
        (tsx, "App.tsx"),
        (i18n, "i18n.py"),
        (guide, "CGAB_USER_GUIDE.md"),
    ):
        assert DUAL_BOOK_CUE in blob, label
        assert LAB_BOOK_DIR_LABEL.rstrip("/") in blob, label
        assert BROKER_BOOK_DIR_LABEL in blob, label
        assert "not the same book" in blob.lower(), label
    assert 'id="booksBanner"' in html
    assert 'id="booksBanner"' in tsx
    assert 'data-i18n="desk.books"' in html
    assert html.index('id="booksBanner"') < html.index('id="root"')
    assert tsx.index('id="booksBanner"') < tsx.index('{page === "desk"')
    assert DUAL_BOOK_CUE_ZH in html
    assert DUAL_BOOK_CUE_ZH in i18n
    assert DUAL_BOOK_CUE_ZH in guide
    assert 't("portfolio.dual_book"' in streamlit
    assert "st.warning(t(\"portfolio.dual_book\"" in streamlit
    assert "st.warning(t(\"trading.dual_book\"" in streamlit
    assert "st.warning(t(\"trading.dual_book\"" in dash
    assert i18n.count('"portfolio.dual_book"') == 3
    assert i18n.count('"trading.dual_book"') == 3


def test_d7_api_envelope_when_dirs_split(honesty_api):
    env = dual_book_envelope()
    assert env["dual_books"] is True
    assert env["design_open"] is True
    assert env["lab_book_dir"] == LAB_BOOK_DIR_LABEL
    assert env["broker_book_dir"] == BROKER_BOOK_DIR_LABEL
    assert env["disclaimer"] == DUAL_BOOK_CUE
    assert env["disclaimer_zh"] == DUAL_BOOK_CUE_ZH
    assert env["verified"] is False
    assert env["live"] is False

    ops = honesty_api["client"].get("/api/ops")
    assert ops.status_code == 200, ops.text
    books = ops.json()["books"]
    assert books["dual_books"] is True
    assert books["disclaimer"] == DUAL_BOOK_CUE
    assert books.get("verified") is not True
    assert books.get("live") is not True
    assert ops.json().get("verified") is not True
    assert ops.json().get("live") is not True

    journal = honesty_api["client"].get("/api/journal")
    assert journal.status_code == 200, journal.text
    body = journal.json()
    assert body["books"]["dual_books"] is True
    assert body["books"]["disclaimer"] == DUAL_BOOK_CUE
    assert body.get("verified") is not True
    assert body.get("live") is not True

    guide = honesty_api["client"].get("/api/guide")
    assert guide.status_code == 200, guide.text
    steps = guide.json()["steps"]
    books_step = next(step for step in steps if step["id"] == "books")
    assert DUAL_BOOK_CUE in books_step["en"]["body"]
    assert DUAL_BOOK_CUE_ZH in books_step["zh"]["body"]
    desk = next(step for step in steps if step["id"] == "desk")
    assert DUAL_BOOK_CUE in desk["en"]["body"]
    assert DUAL_BOOK_CUE_ZH in desk["zh"]["body"]


def test_d7_unified_dirs_must_not_keep_claiming_two_books(monkeypatch, tmp_path):
    """If dirs later merge, the envelope must stop claiming a split."""
    from backend.api import desk_display as dd

    monkeypatch.setattr(dd, "lab_book_dir", lambda: tmp_path.resolve())
    monkeypatch.setattr(dd, "broker_book_dir", lambda: tmp_path.resolve())
    assert dd.dual_books_split() is False
    env = dd.dual_book_envelope()
    assert env["dual_books"] is False
    assert env["design_open"] is False
    assert env["disclaimer"] != DUAL_BOOK_CUE
    assert "not the same book" not in env["disclaimer"].lower()
    assert env["verified"] is False
    assert env["live"] is False
