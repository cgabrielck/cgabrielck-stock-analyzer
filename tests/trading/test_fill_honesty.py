"""O1–O6 fill-price honesty fail-cases (issue #10 Suggested P0 fill chain).

Would have been dishonest success on main before this slice:
  O1 shadow market / $0 → FILLED
  O2 broker map dropped filled_avg_price
  O3 sync promoted FILLED without a price
  O4 performance used limit as fill
  O6 worker notify used entry/limit/0 as fill
O5 (Desk vs worker dual path) is out of scope.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pandas as pd

from backend.api.desk_display import orders_book
from backend.api import paper_performance as pp
from backend.trading.engine.order_manager import InMemoryOrderStore, OrderManager
from backend.trading.engine.shadow import ShadowTradingEngine
from backend.trading.fill_honesty import (
    fill_notify_price,
    honest_fill_status,
    honest_order_row,
    recorded_fill_price,
)
from backend.trading.models import Order, OrderSide, OrderStatus, OrderType
from backend.trading.performance.tracker import compute_equity_curve, compute_trades_pnl


def _order(**kwargs) -> Order:
    defaults = dict(
        id="ord-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=10,
        limit_price=150.0,
        idempotency_key="idem-1",
        status=OrderStatus.SUBMITTED,
    )
    defaults.update(kwargs)
    return Order(**defaults)


# --- helper contract --------------------------------------------------------


def test_o4_recorded_fill_ignores_limit_entry_and_zero():
    assert recorded_fill_price({"limit_price": 100.0, "filled_avg_price": None}) is None
    assert recorded_fill_price({"entry_price": 50.0, "status": "filled"}) is None
    assert recorded_fill_price({"filled_avg_price": 0}) is None
    assert recorded_fill_price({"filled_avg_price": 0.0, "limit_price": 189.5}) is None
    assert recorded_fill_price({"filled_avg_price": 189.5, "limit_price": 100.0}) == 189.5
    assert fill_notify_price(_order(filled_avg_price=None, limit_price=100.0)) is None
    assert honest_fill_status("filled", {"filled_avg_price": None, "limit_price": 100}) == "submitted"
    assert honest_fill_status("filled", {"filled_avg_price": 12.3}) == "filled"


# --- O2 broker map ----------------------------------------------------------


def test_o2_get_order_maps_real_fill_price():
    from backend.trading.alpaca_broker import AlpacaBroker

    broker = AlpacaBroker()
    broker._verified = True
    broker._api = MagicMock()
    now = datetime.now(timezone.utc)
    alpaca = MagicMock()
    alpaca.id = "brk-fill"
    alpaca.client_order_id = "idem-fill"
    alpaca.symbol = "MSFT"
    alpaca.side = "buy"
    alpaca.type = "limit"
    alpaca.qty = "4"
    alpaca.status = "filled"
    alpaca.limit_price = "400.00"
    alpaca.stop_price = None
    alpaca.filled_avg_price = "412.25"
    alpaca.submitted_at = now
    alpaca.filled_at = now
    alpaca.canceled_at = None
    alpaca.failed_at = None
    broker._api.get_order_by_id.return_value = alpaca

    order = broker.get_order("brk-fill")
    assert order is not None
    assert order.status == OrderStatus.FILLED
    assert order.filled_avg_price == 412.25
    assert order.limit_price == 400.0
    assert order.filled_avg_price != order.limit_price


def test_o2_get_order_filled_without_price_is_not_filled():
    """Fail-case: broker says filled, avg missing/zero → cannot escalate to filled."""
    from backend.trading.alpaca_broker import AlpacaBroker

    broker = AlpacaBroker()
    broker._verified = True
    broker._api = MagicMock()
    now = datetime.now(timezone.utc)
    alpaca = MagicMock()
    alpaca.id = "brk-zero"
    alpaca.client_order_id = "idem-zero"
    alpaca.symbol = "MSFT"
    alpaca.side = "buy"
    alpaca.type = "limit"
    alpaca.qty = "4"
    alpaca.status = "filled"
    alpaca.limit_price = "400.00"
    alpaca.stop_price = None
    alpaca.filled_avg_price = None
    alpaca.submitted_at = now
    alpaca.filled_at = now
    alpaca.canceled_at = None
    alpaca.failed_at = None
    broker._api.get_order_by_id.return_value = alpaca

    order = broker.get_order("brk-zero")
    assert order is not None
    assert order.status != OrderStatus.FILLED
    assert order.status != OrderStatus.PARTIALLY_FILLED
    assert order.filled_avg_price is None
    assert order.limit_price == 400.0


def test_o2_get_order_zero_fill_is_not_filled():
    from backend.trading.alpaca_broker import AlpacaBroker

    broker = AlpacaBroker()
    broker._verified = True
    broker._api = MagicMock()
    alpaca = MagicMock()
    alpaca.id = "brk-0"
    alpaca.client_order_id = "idem-0"
    alpaca.symbol = "AAPL"
    alpaca.side = "buy"
    alpaca.type = "market"
    alpaca.qty = "1"
    alpaca.status = "filled"
    alpaca.limit_price = None
    alpaca.stop_price = None
    alpaca.filled_avg_price = "0"
    alpaca.submitted_at = None
    alpaca.filled_at = None
    alpaca.canceled_at = None
    alpaca.failed_at = None
    broker._api.get_order_by_id.return_value = alpaca

    order = broker.get_order("brk-0")
    assert order.status != OrderStatus.FILLED
    assert order.filled_avg_price is None


# --- O3 sync ----------------------------------------------------------------


def test_o3_sync_copies_real_fill_price():
    store = InMemoryOrderStore()
    broker = MagicMock()
    manager = OrderManager(broker=broker, store=store)
    local = _order(id="sync-ok", status=OrderStatus.SUBMITTED)
    store.save_order(local)
    broker_order = local.model_copy()
    broker_order.status = OrderStatus.FILLED
    broker_order.filled_avg_price = 9.87
    broker_order.filled_at = datetime.now(timezone.utc)
    broker.get_order.return_value = broker_order

    result = manager.sync_order_status("sync-ok")
    assert result.status == OrderStatus.FILLED
    assert result.filled_avg_price == 9.87
    assert store.get_order("sync-ok").filled_avg_price == 9.87


def test_o3_sync_does_not_promote_filled_without_price():
    """Fail-case: broker FILLED + avg=None → local stays unfilled, fills empty."""
    store = InMemoryOrderStore()
    broker = MagicMock()
    manager = OrderManager(broker=broker, store=store)
    local = _order(id="sync-bad", status=OrderStatus.SUBMITTED, limit_price=100.0)
    store.save_order(local)
    broker_order = local.model_copy()
    broker_order.status = OrderStatus.FILLED
    broker_order.filled_avg_price = None
    broker_order.filled_at = datetime.now(timezone.utc)
    broker.get_order.return_value = broker_order

    result = manager.sync_order_status("sync-bad")
    assert result.status != OrderStatus.FILLED
    assert result.filled_avg_price is None
    saved = store.get_order("sync-bad")
    assert saved.status != OrderStatus.FILLED
    assert saved.filled_avg_price is None


def test_o3_sync_does_not_copy_limit_as_fill():
    store = InMemoryOrderStore()
    broker = MagicMock()
    manager = OrderManager(broker=broker, store=store)
    local = _order(id="sync-limit", status=OrderStatus.SUBMITTED, limit_price=349.39)
    store.save_order(local)
    broker_order = local.model_copy()
    broker_order.status = OrderStatus.FILLED
    broker_order.filled_avg_price = 0.0
    broker.get_order.return_value = broker_order

    result = manager.sync_order_status("sync-limit")
    assert result.status != OrderStatus.FILLED
    assert result.filled_avg_price is None
    assert result.limit_price == 349.39


def test_o3_sync_demotes_local_filled_without_avg():
    """Residual: already FILLED + avg=None must not skip broker / keep ledger filled."""
    store = InMemoryOrderStore()
    broker = MagicMock()
    manager = OrderManager(broker=broker, store=store)
    local = _order(
        id="sync-stale",
        status=OrderStatus.FILLED,
        filled_avg_price=None,
        limit_price=100.0,
    )
    store.save_order(local)
    broker.get_order.return_value = None

    result = manager.sync_order_status("sync-stale")
    broker.get_order.assert_called_once()
    assert result.status != OrderStatus.FILLED
    assert result.filled_avg_price is None
    saved = store.get_order("sync-stale")
    assert saved.status != OrderStatus.FILLED
    assert saved.filled_avg_price is None


def test_o3_sync_skips_filled_with_real_price():
    store = InMemoryOrderStore()
    broker = MagicMock()
    manager = OrderManager(broker=broker, store=store)
    local = _order(id="sync-done", status=OrderStatus.FILLED, filled_avg_price=12.5)
    store.save_order(local)
    result = manager.sync_order_status("sync-done")
    broker.get_order.assert_not_called()
    assert result.status == OrderStatus.FILLED
    assert result.filled_avg_price == 12.5


def test_o3_sync_recovers_real_fill_for_unpriced_local_filled():
    store = InMemoryOrderStore()
    broker = MagicMock()
    manager = OrderManager(broker=broker, store=store)
    local = _order(
        id="sync-recover",
        status=OrderStatus.FILLED,
        filled_avg_price=None,
        limit_price=100.0,
    )
    store.save_order(local)
    broker_order = local.model_copy()
    broker_order.status = OrderStatus.FILLED
    broker_order.filled_avg_price = 9.87
    broker.get_order.return_value = broker_order

    result = manager.sync_order_status("sync-recover")
    assert result.status == OrderStatus.FILLED
    assert result.filled_avg_price == 9.87
    assert result.filled_avg_price != result.limit_price


# --- O1 shadow --------------------------------------------------------------


def test_o1_shadow_market_no_bars_is_not_filled_at_zero():
    """Fail-case: next_bars=None + market → must not FILLED @ 0."""
    engine = ShadowTradingEngine()
    order = _order(
        order_type=OrderType.MARKET,
        limit_price=None,
        status=OrderStatus.RISK_APPROVED,
    )
    result = engine.simulate_submission(order, next_bars=None)
    assert result.status != OrderStatus.FILLED
    assert result.filled_avg_price is None


def test_o1_shadow_zero_limit_is_not_filled():
    engine = ShadowTradingEngine()
    order = _order(limit_price=0.0, status=OrderStatus.RISK_APPROVED)
    result = engine.simulate_submission(order, next_bars=None)
    assert result.status != OrderStatus.FILLED
    assert result.filled_avg_price is None


def test_o1_shadow_market_zero_open_does_not_fall_back_to_limit():
    engine = ShadowTradingEngine()
    order = _order(
        order_type=OrderType.MARKET,
        limit_price=150.0,
        status=OrderStatus.RISK_APPROVED,
    )
    bars = pd.DataFrame({"Open": [0.0], "Low": [0.0], "High": [0.0], "Close": [0.0]})
    result = engine.simulate_submission(order, next_bars=bars)
    assert result.status != OrderStatus.FILLED
    assert result.filled_avg_price is None


def test_o1_shadow_market_leftover_limit_is_not_a_fill_without_bars():
    """Market does not send limit to the broker — leftover limit is not a fill."""
    engine = ShadowTradingEngine()
    order = _order(
        order_type=OrderType.MARKET,
        limit_price=150.0,
        status=OrderStatus.RISK_APPROVED,
    )
    result = engine.simulate_submission(order, next_bars=None)
    assert result.status != OrderStatus.FILLED
    assert result.filled_avg_price is None


def test_o8_shadow_limit_no_bars_is_not_filled_at_limit():
    """Cursor BLOCK: no bars → must not store FILLED @ limit as filled_avg_price."""
    engine = ShadowTradingEngine()
    order = _order(limit_price=150.0, status=OrderStatus.RISK_APPROVED)
    result = engine.simulate_submission(order, next_bars=None)
    assert result.status != OrderStatus.FILLED
    assert result.filled_avg_price is None
    saved = None
    if engine.order_manager is not None:
        saved = engine.order_manager.store.get_order(order.id)
    assert saved is None or saved.status != OrderStatus.FILLED


def test_o8_shadow_stop_limit_no_bars_is_not_filled_at_limit():
    engine = ShadowTradingEngine()
    order = _order(
        order_type=OrderType.STOP_LIMIT,
        limit_price=150.0,
        status=OrderStatus.RISK_APPROVED,
    )
    result = engine.simulate_submission(order, next_bars=None)
    assert result.status != OrderStatus.FILLED
    assert result.filled_avg_price is None


def test_o8_worker_empty_ohlcv_does_not_synthesize_ref_bar():
    """Probe: empty OHLCV must not invent Open=ref so market fills at ref*1.0005."""
    from backend.trading.engine.worker import TradingWorker

    worker = TradingWorker.__new__(TradingWorker)
    worker._fetch_ohlcv = lambda *a, **k: pd.DataFrame()
    nxt = TradingWorker._fetch_next_bar(worker, "AAPL", 200.0)
    assert nxt is None or getattr(nxt, "empty", False)

    engine = ShadowTradingEngine()
    market = _order(
        order_type=OrderType.MARKET,
        limit_price=None,
        status=OrderStatus.RISK_APPROVED,
    )
    mres = engine.simulate_submission(market, next_bars=nxt)
    assert mres.status != OrderStatus.FILLED
    assert mres.filled_avg_price is None

    limit = _order(limit_price=200.0, status=OrderStatus.RISK_APPROVED)
    lres = engine.simulate_submission(limit, next_bars=nxt)
    assert lres.status != OrderStatus.FILLED
    assert lres.filled_avg_price is None


# --- O4 performance / digest ------------------------------------------------


def test_o4_turnover_does_not_use_limit_as_fill():
    orders = [
        {
            "status": "filled",
            "quantity": 10,
            "filled_avg_price": None,
            "limit_price": 100.0,
        }
    ]
    # Would have been 100% of equity ($1000 / $1000) if limit stood in.
    assert pp._turnover_pct(orders, 1000.0) == 0.0


def test_o4_normalize_does_not_promote_limit_to_fill():
    row = pp._normalize_paper_order(
        {
            "symbol": "GOOGL",
            "side": "buy",
            "status": "filled",
            "quantity": 28,
            "limit_price": 349.39,
            "filled_avg_price": None,
        }
    )
    assert row["filled_avg_price"] is None
    assert row["status"] != "filled"
    assert row["limit_price"] == 349.39


def test_o4_equity_curve_skips_unpriced_fill():
    orders = [
        {
            "symbol": "AAPL",
            "side": "buy",
            "status": "filled",
            "quantity": 10,
            "filled_avg_price": None,
            "limit_price": 100.0,
            "filled_at": "2026-08-01T15:00:00+00:00",
        }
    ]
    df = compute_equity_curve(orders, 100_000)
    assert len(df) == 0
    trades = compute_trades_pnl(orders)
    assert trades == []


def test_o4_order_brief_does_not_claim_limit_as_fill():
    brief = pp._order_brief(
        {
            "symbol": "AAPL",
            "status": "filled",
            "quantity": 1,
            "limit_price": 349.39,
            "filled_avg_price": None,
        }
    )
    assert brief["status"] != "filled"
    assert brief["filled_avg_price"] is None
    assert brief["limit_price"] == 349.39


def test_o4_digest_unpriced_fill_is_not_listed_filled(monkeypatch):
    monkeypatch.setattr(
        "backend.trading.alpaca_broker.AlpacaBroker",
        lambda: (_ for _ in ()).throw(RuntimeError("skip")),
    )
    digest = pp.build_daily_digest(
        paper={"empty_book": True, "benchmark": {}},
        as_of_day=date(2026, 9, 15),
        orders=[
            {
                "symbol": "MSFT",
                "side": "buy",
                "status": "filled",
                "quantity": 4,
                "limit_price": 400.0,
                "filled_avg_price": None,
                "filled_at": "2026-09-15T18:00:00+00:00",
            }
        ],
        worker_status={"strategy": "stable", "running": False, "skip_counts": {}},
        scan_payload={"available": False, "stale": True, "top5_tickers": []},
    )
    assert digest["filled"]["count"] == 0
    assert "MSFT" not in digest["filled"]["symbols"]


# --- O6 notify + Desk display ----------------------------------------------


def test_o6_fill_notify_price_rejects_limit_and_zero():
    assert fill_notify_price(_order(status=OrderStatus.FILLED, filled_avg_price=None, limit_price=100)) is None
    assert fill_notify_price(_order(status=OrderStatus.FILLED, filled_avg_price=0.0, limit_price=100)) is None
    assert fill_notify_price(_order(status=OrderStatus.FILLED, filled_avg_price=12.5, limit_price=100)) == 12.5


def test_o6_worker_fill_notify_source_does_not_use_limit_fallback():
    from pathlib import Path

    text = Path("backend/trading/engine/worker.py").read_text(encoding="utf-8")
    assert "filled_avg_price or updated.limit_price" not in text
    assert "filled_avg_price or signal.entry_price" not in text
    assert "fill_notify_price" in text


def test_desk_orders_book_does_not_show_unpriced_fill_as_filled():
    book = orders_book(
        [
            {
                "id": "row-1",
                "symbol": "AAPL",
                "status": "filled",
                "limit_price": 150.0,
                "filled_avg_price": None,
            }
        ],
        source="broker",
        degraded=False,
        unverified=False,
    )
    row = book["orders"][0]
    assert row["status"] != "filled"
    assert row["filled_avg_price"] is None
    assert book.get("verified") is not True
    assert book.get("live") is not True


def test_honest_order_row_keeps_real_fill():
    row = honest_order_row(
        {"id": "ok", "status": "filled", "filled_avg_price": 11.5, "limit_price": 10.0}
    )
    assert row["status"] == "filled"
    assert row["filled_avg_price"] == 11.5


def test_get_orders_unpriced_broker_fill_is_not_shown_filled(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.trading.engine.order_manager import InMemoryOrderStore

    monkeypatch.setattr("backend.trading.telegram_inbox.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.scan_scheduler.start_background", lambda: (lambda: None))
    monkeypatch.setenv("APCA_PAPER", "true")
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "unit-test-operator")
    broker = MagicMock()
    store = InMemoryOrderStore()
    from backend.api import app as app_mod

    monkeypatch.setattr(app_mod, "_broker", broker)
    monkeypatch.setattr(app_mod, "_store", store)
    row = MagicMock()
    row.id = "brk-unpriced"
    row.symbol = "AAPL"
    row.side = "buy"
    row.type = "limit"
    row.qty = 1
    row.status = "filled"
    row.limit_price = 150.0
    row.filled_avg_price = None
    row.submitted_at = None
    broker.api.get_orders.return_value = [row]
    with TestClient(app_mod.app, raise_server_exceptions=False) as client:
        client.headers.update({"X-Desk-Operator-Token": "unit-test-operator"})
        res = client.get("/api/orders")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["orders"][0]["id"] == "brk-unpriced"
    assert body["orders"][0]["status"] != "filled"
    assert body["orders"][0]["filled_avg_price"] is None
    assert body.get("verified") is not True
    assert body.get("live") is not True
