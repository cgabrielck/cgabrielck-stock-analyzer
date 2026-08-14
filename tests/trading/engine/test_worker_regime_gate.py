"""Integration test for the TradingWorker market-regime exposure gate.

When the detected regime's target_allocation is already met or exceeded by the
current invested value, the worker must NOT open new positions — it should emit
a REGIME_GATE summary row and submit no buy orders.

Uses mocked broker/manager and synthetic price data so the test is offline and
deterministic.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from backend.trading.engine.worker import TradingWorker
from backend.trading.models import AccountSummary, Position


def _synthetic_df(n_days=260, start_price=100.0, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n_days)
    returns = rng.normal(0.0006, 0.014, n_days)
    close = start_price * np.cumprod(1 + returns)
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.004, n_days))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.004, n_days))
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close,
         "Volume": rng.integers(1_000_000, 5_000_000, n_days)},
        index=dates,
    )


def _build_worker():
    broker = MagicMock()
    store = MagicMock()
    manager = MagicMock()
    reconciler = MagicMock()
    worker = TradingWorker(
        broker=broker, store=store, manager=manager, reconciler=reconciler,
        strategy_id="stable", ticker_universe=["AAPL", "MSFT"],
    )
    return worker, manager


class TestWorkerRegimeGate(unittest.TestCase):

    def test_exposure_gate_blocks_new_entries_when_over_target(self):
        worker, manager = _build_worker()

        # Bear regime caps total exposure at 40%.
        bear_regime = {"regime": "bear", "target_allocation": 0.40}

        # Account already 50% invested (>40% cap) via a GOOG position. GOOG is
        # intentionally NOT in price_history, so no exit check runs on it and its
        # mark falls back to average_entry_price (100) — a deterministic 50%.
        positions = [Position(id="p1", symbol="GOOG", quantity=500.0,
                              average_entry_price=100.0)]
        account = AccountSummary(cash=50_000.0, buying_power=50_000.0,
                                 portfolio_value=100_000.0, positions=positions)

        price_data = {"AAPL": _synthetic_df(seed=1), "MSFT": _synthetic_df(seed=2)}

        with patch.object(worker, "_detect_regime", return_value=bear_regime), \
             patch.object(worker, "_fetch_vix", return_value=None), \
             patch.object(worker, "_fetch_ohlcv", side_effect=lambda t, **k: price_data.get(t)), \
             patch.object(worker, "_get_fundamental_score", return_value=80.0):
            worker._run_strategy_signals(account)

        # No new BUY orders while over the exposure cap. Exits (sells) are still
        # allowed — closing a position must never be blocked by the regime gate.
        from backend.trading.models import OrderSide
        buy_orders = [
            c.args[0] for c in manager.submit_new_order.call_args_list
            if c.args and getattr(c.args[0], "side", None) == OrderSide.BUY
        ]
        self.assertEqual(buy_orders, [])
        actions = {row.get("action") for row in worker.last_signal_summary}
        self.assertIn("REGIME_GATE", actions)
        self.assertEqual(worker.last_regime, bear_regime)

    def test_bull_regime_allows_entries_below_target(self):
        worker, manager = _build_worker()

        bull_regime = {"regime": "bull", "target_allocation": 0.90}

        # Only 10% invested — well under the 90% cap, so entries are allowed.
        positions = [Position(id="p1", symbol="AAPL", quantity=100.0,
                              average_entry_price=100.0)]
        account = AccountSummary(cash=90_000.0, buying_power=90_000.0,
                                 portfolio_value=100_000.0, positions=positions)

        price_data = {"AAPL": _synthetic_df(seed=1), "MSFT": _synthetic_df(seed=2)}

        with patch.object(worker, "_detect_regime", return_value=bull_regime), \
             patch.object(worker, "_fetch_vix", return_value=None), \
             patch.object(worker, "_fetch_ohlcv", side_effect=lambda t, **k: price_data.get(t)), \
             patch.object(worker, "_get_fundamental_score", return_value=80.0):
            worker._run_strategy_signals(account)

        # The exposure gate must NOT have fired (entries permitted).
        actions = {row.get("action") for row in worker.last_signal_summary}
        self.assertNotIn("REGIME_GATE", actions)


if __name__ == "__main__":
    unittest.main()
