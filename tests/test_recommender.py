import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents import recommender
from utils.selection import MIN_RECOMMENDATION_METRICS


def test_recommender_uses_shared_minimum_metrics() -> None:
    assert recommender.MIN_RECOMMENDATION_METRICS is MIN_RECOMMENDATION_METRICS


def test_analysis_resorts_after_technical_scores_and_preserves_sector_limit(monkeypatch) -> None:
    stocks = [
        {"ticker": "A", "sector": "Tech", "growth_score": 70, "total_score": 70, "metrics_used": 6},
        {"ticker": "B", "sector": "Health", "growth_score": 80, "total_score": 80, "metrics_used": 6},
        {"ticker": "C", "sector": "Tech", "growth_score": 79, "total_score": 79, "metrics_used": 6},
        {"ticker": "D", "sector": "Tech", "growth_score": 30, "total_score": 30, "metrics_used": 6},
        {"ticker": "E", "sector": "Energy", "growth_score": 58, "total_score": 58, "metrics_used": 6},
        {"ticker": "F", "sector": "Finance", "growth_score": 57, "total_score": 57, "metrics_used": 6},
        {"ticker": "G", "sector": "Utilities", "growth_score": 29, "total_score": 29, "metrics_used": 6},
    ]
    technical = {
        "A": {"technical_score": 0},
        "B": {"technical_score": 100},
        "C": {"technical_score": 100},
        "D": {"technical_score": 100},
        "E": {"technical_score": 100},
        "F": {"technical_score": 100},
        "G": {"technical_score": 100},
    }

    monkeypatch.setattr(recommender, "fetch_all_stocks", lambda *args, **kwargs: {})
    monkeypatch.setattr(recommender, "calculate_all_scores", lambda *args, **kwargs: stocks)
    monkeypatch.setattr(recommender, "compute_all_technical", lambda *args, **kwargs: technical)
    monkeypatch.setattr(recommender, "llm_available", lambda: False)
    monkeypatch.setattr(recommender, "get_latest_filing", lambda *args, **kwargs: {})
    monkeypatch.setattr(recommender, "fetch_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(recommender, "fetch_risk_metrics", lambda *args, **kwargs: {})
    monkeypatch.setattr(recommender, "detect_global_market_regime", lambda **kwargs: {
        "regime": "bull", "entry_threshold": 60.0, "fill_threshold": 50.0, "target_allocation": 0.9,
    })
    monkeypatch.setattr(recommender, "STOCK_UNIVERSE", [
        {"ticker": stock["ticker"], "name_cn": stock["ticker"], "sector": stock["sector"], "universe_tier": "core"}
        for stock in stocks
    ])
    monkeypatch.setattr(recommender.agent_state, "log_recommendation", lambda *args: None)
    monkeypatch.setattr(recommender.agent_state, "log_upgrade", lambda *args: None)

    result = recommender.run_full_analysis()

    assert [stock["ticker"] for stock in result["scored_data"]] == ["B", "C", "E", "F", "D", "G", "A"]
    picks = result["recommendations"]
    assert [stock["ticker"] for stock in picks] == ["B", "C", "E", "F", "D"]
    assert sum(stock["sector"] == "Tech" for stock in picks) == 2


def test_rankings_copy_quality_split_columns(monkeypatch) -> None:
    stocks = [
        {
            "ticker": "A",
            "sector": "Tech",
            "growth_score": 70,
            "total_score": 70,
            "metrics_used": 6,
            "quality_score": 61,
            "quality_roe_score": 80,
            "quality_margin_score": 40,
            "quality_leverage_score": 90,
            "growth_component_score": 55,
            "value_component_score": 70,
        },
    ]
    technical = {"A": {"technical_score": 50}}
    monkeypatch.setattr(recommender, "fetch_all_stocks", lambda *args, **kwargs: {})
    monkeypatch.setattr(recommender, "calculate_all_scores", lambda *args, **kwargs: stocks)
    monkeypatch.setattr(recommender, "compute_all_technical", lambda *args, **kwargs: technical)
    monkeypatch.setattr(recommender, "llm_available", lambda: False)
    monkeypatch.setattr(recommender, "get_latest_filing", lambda *args, **kwargs: {})
    monkeypatch.setattr(recommender, "fetch_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(recommender, "fetch_risk_metrics", lambda *args, **kwargs: {})
    monkeypatch.setattr(recommender, "detect_global_market_regime", lambda **kwargs: {
        "regime": "bull", "entry_threshold": 60.0, "fill_threshold": 50.0, "target_allocation": 0.9,
    })
    monkeypatch.setattr(recommender, "STOCK_UNIVERSE", [
        {"ticker": "A", "name_cn": "A", "sector": "Tech", "universe_tier": "core"},
    ])
    monkeypatch.setattr(recommender.agent_state, "log_recommendation", lambda *args: None)
    monkeypatch.setattr(recommender.agent_state, "log_upgrade", lambda *args: None)

    result = recommender.run_full_analysis()
    row = result["all_rankings"][0]
    assert row["quality_score"] == 61
    assert row["quality_roe_score"] == 80
    assert row["quality_leverage_score"] == 90
