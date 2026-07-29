import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents.market_regime import classify_market_regime


def test_bull_regime_uses_full_target_allocation() -> None:
    spy = pd.Series(list(range(100, 300)))

    result = classify_market_regime(spy, pd.Series([14.0]))

    assert result["regime"] == "bull"
    assert result["target_allocation"] == 0.9
    assert result["pillar_weights"]["trend"] == 0.30
    assert sum(result["pillar_weights"].values()) == 1.0


def test_high_vix_overrides_bull_trend() -> None:
    spy = pd.Series(list(range(100, 300)))

    result = classify_market_regime(spy, pd.Series([30.0]))

    assert result["regime"] == "high_volatility"
    assert result["entry_threshold"] == 75.0
    assert result["target_allocation"] == 0.4
    assert result["pillar_weights"]["risk"] == 0.40


def test_insufficient_history_returns_neutral_unavailable() -> None:
    result = classify_market_regime(pd.Series([100.0] * 50))

    assert result["regime"] == "neutral"
    assert result["available"] is False
