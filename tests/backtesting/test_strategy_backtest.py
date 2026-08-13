"""Tests for the strategy backtest engine (stable/aggressive/hybrid).

Uses synthetic OHLCV data so tests are deterministic and offline.
"""
import os
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from backend.backtesting.strategy_backtest import (
    run_strategy_backtest,
    StrategyBacktestResult,
    WARMUP_DAYS,
)


def _synthetic_data(n_days=400, start_price=100.0, seed=42, drift=0.0005, vol=0.015):
    """Generate a trending-when-needed OHLCV frame."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n_days)
    returns = rng.normal(drift, vol, n_days)
    close = start_price * np.cumprod(1 + returns)
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.005, n_days))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.005, n_days))
    volume = rng.integers(1_000_000, 5_000_000, n_days)
    return pd.DataFrame({
        "Open": open_, "High": high, "Low": low,
        "Close": close, "Volume": volume,
    }, index=dates)


def _synthetic_bear_data(n_days=400, start_price=200.0, seed=7):
    """A strongly down-trending series to force stop-loss exits."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n_days)
    returns = rng.normal(-0.004, 0.015, n_days)
    close = start_price * np.cumprod(1 + returns)
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.004, n_days))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.004, n_days))
    return pd.DataFrame({
        "Open": open_, "High": high, "Low": low,
        "Close": close, "Volume": rng.integers(500_000, 2_000_000, n_days),
    }, index=dates)


def _make_price_data():
    return {
        "AAPL": _synthetic_data(seed=1),
        "MSFT": _synthetic_data(seed=2, start_price=300.0),
        "NVDA": _synthetic_data(seed=3, start_price=50.0),
    }


class TestStrategyBacktest(unittest.TestCase):

    def test_result_shape(self):
        result = StrategyBacktestResult()
        result.trades = [{"pnl": 1.0}, {"pnl": -0.5}]
        result.equity_curve = [{"date": "x", "equity": 100.0}]
        d = result.to_dict()
        self.assertIn("num_trades", d)
        self.assertIn("win_rate_pct", d)

    @patch("backend.backtesting.strategy_backtest.fetch_price_data", return_value=_make_price_data())
    def test_stable_runs_and_returns_metrics(self, mock_fetch):
        result = run_strategy_backtest(
            strategy_id="stable",
            start=(pd.Timestamp.now() - pd.DateOffset(days=400)).strftime("%Y-%m-%d"),
            end=pd.Timestamp.now().strftime("%Y-%m-%d"),
            tickers=["AAPL", "MSFT", "NVDA"],
            initial_capital=100_000.0,
        )
        self.assertIsInstance(result, StrategyBacktestResult)
        self.assertTrue(len(result.equity_curve) > 30)
        # Metrics should be populated even with zero trades.
        self.assertIsInstance(result.total_return_pct, float)
        self.assertIsInstance(result.max_drawdown_pct, float)

    @patch("backend.backtesting.strategy_backtest.fetch_price_data", return_value=_make_price_data())
    def test_aggressive_runs(self, mock_fetch):
        result = run_strategy_backtest(
            strategy_id="aggressive",
            start=(pd.Timestamp.now() - pd.DateOffset(days=400)).strftime("%Y-%m-%d"),
            end=pd.Timestamp.now().strftime("%Y-%m-%d"),
            tickers=["AAPL", "MSFT"],
        )
        self.assertEqual(result.num_trades, len(result.trades))

    @patch("backend.backtesting.strategy_backtest.fetch_price_data", return_value={
        "AAPL": _synthetic_bear_data(),
    })
    def test_bear_market_never_enters_unsafely(self, mock_fetch):
        # Stable requires price > SMA200, which a down-trending series fails,
        # so the strategy must NOT open losing positions in a bear market.
        result = run_strategy_backtest(
            strategy_id="stable",
            start=(pd.Timestamp.now() - pd.DateOffset(days=400)).strftime("%Y-%m-%d"),
            end=pd.Timestamp.now().strftime("%Y-%m-%d"),
            tickers=["AAPL"],
        )
        # Either zero trades (filtered by trend) or if any trade happened it must
        # respect the stop-loss discipline.
        if result.trades:
            reasons = {t["exit_reason"] for t in result.trades}
            self.assertIn("stop_loss", reasons)

    @patch("backend.backtesting.strategy_backtest.fetch_price_data", return_value={})
    def test_empty_data_returns_warning(self, mock_fetch):
        result = run_strategy_backtest(
            strategy_id="stable",
            start="2020-01-01", end="2020-12-31", tickers=["AAPL"],
        )
        self.assertEqual(result.num_trades, 0)
        self.assertTrue(result.warnings)

    def test_invalid_strategy_falls_back_to_stable(self):
        from backend.trading.strategies.registry import get_strategy
        s = get_strategy("does_not_exist")
        self.assertEqual(s.strategy_id, "stable")


if __name__ == "__main__":
    unittest.main()
