import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents.signal_pillars import calculate_five_pillar_score


def test_five_pillars_score_bullish_setup_and_expose_audit_details() -> None:
    result = calculate_five_pillar_score({
        "price": 120,
        "sma_20": 115,
        "sma_50": 105,
        "sma_200": 90,
        "adx_14": 30,
        "rsi_14": 58,
        "macd_histogram": 2,
        "bb_lower": 100,
        "bb_upper": 130,
        "dollar_volume_ratio": 1.6,
        "volume_quality_score": 75,
        "obv_trend": 1,
        "atr_14": 2,
    }, {
        "available": True,
        "annual_volatility_pct": 18,
        "max_drawdown_pct": 10,
        "beta": 1.0,
    })

    assert result["score"] > 70
    assert result["coverage"] == 1.0
    assert set(result["pillars"]) == {"trend", "momentum", "mean_reversion", "volume", "risk"}
    assert all(pillar["reasons"] for pillar in result["pillars"].values())


def test_missing_pillars_are_renormalized_instead_of_scored_as_neutral() -> None:
    result = calculate_five_pillar_score({
        "price": 120,
        "sma_50": 100,
        "rsi_14": 55,
        "macd_histogram": 1,
    })

    assert result["coverage"] == 0.45
    assert result["pillars"]["mean_reversion"]["available"] is False
    assert result["pillars"]["volume"]["available"] is False
    assert result["pillars"]["risk"]["available"] is False
    assert result["score"] > 70


def test_no_available_pillars_returns_no_score() -> None:
    result = calculate_five_pillar_score({})

    assert result["score"] is None
    assert result["coverage"] == 0.0


def test_pillar_scores_are_clamped_to_valid_range() -> None:
    result = calculate_five_pillar_score({
        "price": 100,
        "sma_20": 200,
        "sma_50": 150,
        "sma_200": 200,
        "adx_14": 50,
        "rsi_14": 90,
        "macd_histogram": -100,
        "bb_lower": 10,
        "bb_upper": 50,
        "dollar_volume_ratio": 0.1,
        "volume_quality_score": -100,
        "obv_trend": -1,
        "atr_14": 20,
    }, {
        "available": True,
        "annual_volatility_pct": 100,
        "max_drawdown_pct": 80,
        "beta": 3,
    })

    assert 0 <= result["score"] <= 100
    assert all(0 <= pillar["score"] <= 100 for pillar in result["pillars"].values())


def test_custom_weights_change_score_and_are_auditable() -> None:
    technical = {
        "price": 120, "sma_50": 100, "rsi_14": 80, "macd_histogram": -1,
    }
    trend_weighted = calculate_five_pillar_score(technical, weights={
        "trend": 0.80, "momentum": 0.05, "mean_reversion": 0.05, "volume": 0.05, "risk": 0.05,
    })
    momentum_weighted = calculate_five_pillar_score(technical, weights={
        "trend": 0.05, "momentum": 0.80, "mean_reversion": 0.05, "volume": 0.05, "risk": 0.05,
    })

    assert trend_weighted["score"] > momentum_weighted["score"]
    assert trend_weighted["weights"]["trend"] == 0.80
