"""Unit tests for strategy parameter overrides and the Aggressive 3R target.

These are deterministic and offline: the Aggressive tests patch detect_vcp so a
breakout is always "found", isolating the target/stop math from pattern noise.
"""
import os
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from backend.trading.strategies.aggressive import AggressiveStrategy
from backend.trading.strategies.stable import StableStrategy


def _uptrend(n_days: int = 120, start_price: float = 100.0, seed: int = 11) -> pd.DataFrame:
    """A steady uptrend so price > SMA50 on the final bar."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n_days)
    returns = rng.normal(0.003, 0.01, n_days)
    close = start_price * np.cumprod(1 + returns)
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    volume = rng.integers(1_000_000, 2_000_000, n_days).astype(float)
    # Force a volume surge on the last bar (breakout confirmation).
    volume[-1] = volume[-20:].mean() * 3.0
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


class TestAggressive3R(unittest.TestCase):
    def test_target_is_three_times_initial_risk(self):
        df = _uptrend()
        close = float(df["Close"].iloc[-1])
        fake_vcp = {"found": True, "contractions": 3, "breakout_level": close * 0.99,
                    "last_pullback_pct": 0.05, "avg_volume_trend": "declining"}
        with patch("backend.trading.strategies.aggressive.detect_vcp", return_value=fake_vcp):
            strat = AggressiveStrategy()
            sig = strat.generate_signal("TEST", df, fundamental_score=60.0,
                                        llm_signal=None, current_positions=[])
        self.assertIsNotNone(sig)
        risk = sig.entry_price - sig.stop_loss_price
        reward = sig.take_profit_price - sig.entry_price
        # Reward should be ~3× the risk (3R rule).
        self.assertAlmostEqual(reward / risk, 3.0, places=2)

    def test_reward_risk_ratio_override(self):
        df = _uptrend()
        close = float(df["Close"].iloc[-1])
        fake_vcp = {"found": True, "contractions": 2, "breakout_level": close * 0.99,
                    "last_pullback_pct": 0.05, "avg_volume_trend": "declining"}
        with patch("backend.trading.strategies.aggressive.detect_vcp", return_value=fake_vcp):
            strat = AggressiveStrategy(reward_risk_ratio=2.0)
            sig = strat.generate_signal("TEST", df, fundamental_score=60.0,
                                        llm_signal=None, current_positions=[])
        self.assertIsNotNone(sig)
        risk = sig.entry_price - sig.stop_loss_price
        reward = sig.take_profit_price - sig.entry_price
        self.assertAlmostEqual(reward / risk, 2.0, places=2)


class TestParameterOverride(unittest.TestCase):
    def test_stable_override_applies(self):
        strat = StableStrategy(rsi_entry=25, stop_loss_pct=0.07)
        self.assertEqual(strat.RSI_ENTRY, 25)
        self.assertEqual(strat.STOP_LOSS_PCT, 0.07)

    def test_stable_override_ignores_unknown_keys(self):
        strat = StableStrategy(not_a_real_param=123)
        self.assertFalse(hasattr(strat, "NOT_A_REAL_PARAM"))

    def test_aggressive_override_applies(self):
        strat = AggressiveStrategy(volume_surge=2.5, trailing_stop_pct=0.10)
        self.assertEqual(strat.VOLUME_SURGE, 2.5)
        self.assertEqual(strat.TRAILING_STOP_PCT, 0.10)


if __name__ == "__main__":
    unittest.main()
