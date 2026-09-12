"""Unit tests for Stage-2 helpers, TrendStrategy, AdaptiveStrategy."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd

from backend.trading.strategies.adaptive import AdaptiveStrategy
from backend.trading.strategies.registry import get_strategy
from backend.trading.strategies.stage2 import (
    add_trend_template_columns,
    classify_spy_regime,
    size_shares_by_risk,
    stage2_ok,
)
from backend.trading.strategies.trend import TrendStrategy


def _stage2_frame(n=260, seed=3):
    """Synthetic Stage-2 uptrend with a late pullback to SMA21."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp("2026-08-01"), periods=n)
    returns = rng.normal(0.0025, 0.012, n)
    close = 80.0 * np.cumprod(1 + returns)
    # Force last bars into a mild pullback then reclaim
    close[-5:] = close[-6] * np.array([0.99, 0.985, 0.982, 0.988, 0.995])
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    vol = rng.integers(800_000, 2_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=dates,
    )


def test_stage2_ok_on_uptrend():
    work = add_trend_template_columns(_stage2_frame())
    ok, code = stage2_ok(work.iloc[-1])
    assert ok is True
    assert code is None


def test_classify_spy_regime_bull():
    dates = pd.bdate_range(end=pd.Timestamp("2026-08-01"), periods=250)
    close = pd.Series(np.linspace(100, 150, 250), index=dates)
    assert classify_spy_regime(close) == "bull"


def test_size_shares_by_risk():
    qty = size_shares_by_risk(
        equity=100_000, cash=50_000, fill_price=100, stop_price=95, risk_pct=0.01
    )
    # risk $1000 / $5 = 200 shares, capped by cash 500
    assert qty == 200


def test_trend_generates_on_stage2_pullback():
    df = _stage2_frame()
    spy = df["Close"] * 0.9
    spy.index = df.index
    strat = TrendStrategy()
    strat.set_spy_close(spy)
    with patch("backend.trading.strategies.trend.stage2_ok", return_value=(True, None)):
        with patch("backend.trading.strategies.trend.pullback_entry_ok", return_value=(True, None)):
            with patch("backend.trading.strategies.trend.near_52w_high", return_value=(True, None)):
                with patch(
                    "backend.trading.strategies.trend.relative_strength_vs_spy",
                    return_value=(0.05, None),
                ):
                    sig = strat.generate_signal("AAA", df, 70, None, [])
    assert sig is not None
    assert sig.strategy_id == "trend"
    assert sig.meta.get("entry_kind") == "trend"
    assert sig.stop_loss_price < sig.entry_price


def test_trend_no_bb_mid_scalp_exit():
    df = _stage2_frame()
    strat = TrendStrategy()
    # Price above stop and above SMA50 → no exit
    work = strat.populate_indicators(df)
    price = float(work["Close"].iloc[-1])
    sma50 = float(work["sma50"].iloc[-1])
    es = strat.check_exit("AAA", price * 0.95, max(price, sma50 * 1.01), work, price * 0.9, price * 1.5)
    assert es is None


def test_adaptive_bear_blocks():
    strat = AdaptiveStrategy()
    dates = pd.bdate_range(end=pd.Timestamp("2026-08-01"), periods=250)
    spy = pd.Series(np.linspace(150, 100, 250), index=dates)
    strat.set_spy_close(spy)
    strat.set_asof(dates[-1])
    assert strat.diagnose_entry("AAA", _stage2_frame(), 70, None, []) == "regime_no_new_buys"


def test_registry_aliases():
    assert get_strategy("hybrid").strategy_id in ("hybrid", "adaptive")
    assert get_strategy("breakout").strategy_id == "aggressive"
    assert get_strategy("trend").strategy_id == "trend"
    assert get_strategy("adaptive").strategy_id == "adaptive"
