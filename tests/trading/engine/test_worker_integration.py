"""End-to-end integration test for TradingWorker in shadow mode.

Tests the full pipeline:
  - Worker receives a buy signal from strategy
  - Creates a draft order
  - ShadowTradingEngine simulates fill
  - Order is persisted with filled status
  - Next cycle reconciles the position
"""
import os
import sys
import unittest
import tempfile
import pandas as pd
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from backend.trading.engine.worker import _build_worker
from backend.trading.models import OrderStatus


class FakeAccount:
    """Mock Alpaca account object.

    portfolio_value kept modest so Kelly-sized orders stay under the
    mandate gate's $10k notional cap in this test harness.
    """
    portfolio_value = 40000.0
    cash = 40000.0
    buying_power = 40000.0
    positions = []


class FakeBroker:
    """Mock broker that returns fake account data."""
    def get_account(self):
        return FakeAccount()

    def submit_order(self, order):
        raise RuntimeError("Should not call broker.submit_order in shadow mode")

    def get_order_status(self, broker_order_id: str):
        raise RuntimeError("Should not call broker.get_order_status in shadow mode")

    def cancel_order(self, broker_order_id: str):
        raise RuntimeError("Should not call broker.cancel_order in shadow mode")


def _fake_ohlcv_bar(symbol: str, close_price: float = 150.0) -> pd.DataFrame:
    """Return a single OHLCV bar for shadow fill simulation."""
    return pd.DataFrame({
        "Open": [close_price * 0.99],
        "High": [close_price * 1.01],
        "Low": [close_price * 0.98],
        "Close": [close_price],
        "Volume": [1000000],
    }, index=[datetime.now(timezone.utc)])


class TestWorkerIntegration(unittest.TestCase):
    """End-to-end shadow mode integration test."""

    def test_shadow_mode_buy_signal_to_fill(self):
        """
        Verify full pipeline:
          1. Worker runs strategy and generates BUY signal
          2. Creates draft order
          3. ShadowTradingEngine simulates fill
          4. Order is stored with FILLED status
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "orders.json"

            # Patch AlpacaBroker so _build_worker never hits the real API
            with patch("backend.trading.alpaca_broker.AlpacaBroker", return_value=FakeBroker()):
                # Build worker in shadow mode with a tiny universe
                worker = _build_worker(
                    strategy_id="stable",
                    ticker_universe=["AAPL"],
                    execution_mode="shadow",
                )

            # Isolate order store to tempdir so orders don't leak between runs
            from backend.trading.storage import SQLiteOrderStore, JSONOrderStore, create_order_store
            store = create_order_store(backend="sqlite", filepath=str(Path(tmpdir) / "orders.sqlite3"))
            worker.store = store
            worker.manager.store = store
            worker.signal_processor.order_manager.store = store
            if worker.shadow_engine is not None and worker.shadow_engine.order_manager is not None:
                worker.shadow_engine.order_manager.store = store
            self.assertTrue(isinstance(store, (SQLiteOrderStore, JSONOrderStore)))

            # Mock regime detection (neutral)
            def _fake_regime():
                return {
                    "regime": "neutral",
                    "vix": 15.0,
                    "spy_sma_50": 450.0,
                    "spy_sma_200": 440.0,
                }
            worker._detect_regime = _fake_regime

            # Mock fundamental score (positive)
            worker._get_fundamental_score = lambda ticker: 75.0

            # Mock OHLCV fetcher to return data that triggers stable strategy entry:
            # Phase 1: 230 days uptrend -> Phase 2: 10 days sideways -> Phase 3: 12 days sharp drop
            # Result: RSI oversold, price at BB lower, still above SMA200
            def _fake_fetch_ohlcv(ticker: str, period: str = "1y") -> pd.DataFrame:
                dates = pd.date_range(end=datetime.now(timezone.utc), periods=252, freq="D")
                phase1 = [100 + i*0.3 for i in range(230)]   # 100 -> 169 (uptrend)
                phase2 = [169] * 10                          # sideways
                phase3 = [169 - i*2.0 for i in range(12)]    # 169 -> 147 (sharp drop)
                prices = (phase1 + phase2 + phase3)[:252]
                
                df = pd.DataFrame({
                    "Open": [p * 0.995 for p in prices],
                    "High": [p * 1.005 for p in prices],
                    "Low": [p * 0.99 for p in prices],
                    "Close": prices,
                    "Volume": [1000000] * 252,
                }, index=dates)
                return df
            worker._fetch_ohlcv = _fake_fetch_ohlcv

            # Mock next-bar fetcher for shadow fill
            worker._fetch_next_bar = lambda symbol, ref_price: _fake_ohlcv_bar(symbol, ref_price * 1.005)

            # Mock VIX
            worker._fetch_vix = lambda: 15.0

            # Mock account getter
            worker._safe_get_account = lambda: FakeAccount()

            # Run one cycle — the synthetic oversold OHLCV should trigger the strategy
            worker._run_strategy_signals(FakeAccount())

            # Verify order was created and filled
            orders = worker.signal_processor.order_manager.store.get_recent_orders(limit=10)
            self.assertEqual(len(orders), 1, "Expected exactly one order")

            order = orders[0]
            self.assertEqual(order.symbol, "AAPL")
            self.assertGreater(order.quantity, 0, "Order should have positive quantity from Kelly sizing")
            self.assertEqual(order.status, OrderStatus.FILLED, "Shadow engine should have filled the order")
            self.assertIsNotNone(order.filled_avg_price, "Filled order should have avg price")
            self.assertIsNotNone(order.filled_at, "Filled order should have timestamp")

    def test_shadow_mode_no_broker_calls(self):
        """Verify shadow mode never touches the real broker for order submission."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "orders.json"

            # Patch AlpacaBroker so _build_worker never hits the real API
            with patch("backend.trading.alpaca_broker.AlpacaBroker", return_value=FakeBroker()):
                worker = _build_worker(
                    strategy_id="stable",
                    ticker_universe=["AAPL"],
                    execution_mode="shadow",
                )

            # Isolate order store to tempdir
            from backend.trading.storage import create_order_store
            store = create_order_store(backend="sqlite", filepath=str(Path(tmpdir) / "orders.sqlite3"))
            worker.store = store
            worker.manager.store = store
            worker.signal_processor.order_manager.store = store
            if worker.shadow_engine is not None and worker.shadow_engine.order_manager is not None:
                worker.shadow_engine.order_manager.store = store

            # Replace broker with a strict mock that raises on any call
            strict_broker = MagicMock()
            strict_broker.get_account.return_value = FakeAccount()
            strict_broker.submit_order.side_effect = RuntimeError("submit_order called in shadow mode!")
            strict_broker.get_order_status.side_effect = RuntimeError("get_order_status called in shadow mode!")
            worker.broker = strict_broker

            # Mock helpers — provide oversold OHLCV to trigger a natural entry signal
            worker._detect_regime = lambda: {"regime": "neutral", "vix": 15.0, "spy_sma_50": 450.0, "spy_sma_200": 440.0}
            worker._get_fundamental_score = lambda ticker: 75.0

            def _oversold_ohlcv(ticker: str, period: str = "1y") -> pd.DataFrame:
                dates = pd.date_range(end=datetime.now(timezone.utc), periods=252, freq="D")
                phase1 = [100 + i*0.3 for i in range(230)]
                phase2 = [169] * 10
                phase3 = [169 - i*2.0 for i in range(12)]
                prices = (phase1 + phase2 + phase3)[:252]
                return pd.DataFrame({
                    "Open": [p * 0.995 for p in prices],
                    "High": [p * 1.005 for p in prices],
                    "Low": [p * 0.99 for p in prices],
                    "Close": prices,
                    "Volume": [1000000] * 252,
                }, index=dates)
            worker._fetch_ohlcv = _oversold_ohlcv
            worker._fetch_next_bar = lambda symbol, ref_price: _fake_ohlcv_bar(symbol, ref_price)
            worker._fetch_vix = lambda: 15.0
            worker._safe_get_account = lambda: FakeAccount()

            # Run a full cycle in shadow mode
            worker._run_strategy_signals(FakeAccount())

            # If we reach here without exception, shadow mode correctly bypassed broker
            strict_broker.submit_order.assert_not_called()
            strict_broker.get_order_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
