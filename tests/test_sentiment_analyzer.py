import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents.recommender import _sentiment_modifier_pct
from agents.sentiment_analyzer import aggregate_news_sentiment, compute_sentiment


def test_empty_news_returns_consistent_neutral_result() -> None:
    result = aggregate_news_sentiment([])

    assert result == {
        "score": 50,
        "label": "neutral",
        "positive_pct": 0,
        "negative_pct": 0,
        "article_count": 0,
    }


def test_news_sentiment_aggregates_positive_negative_and_neutral_articles() -> None:
    result = aggregate_news_sentiment([
        {"sentiment": "positive"},
        {"sentiment": "positive"},
        {"sentiment": "negative"},
        {"sentiment": "neutral"},
    ])

    assert result["score"] == 62
    assert result["label"] == "positive"
    assert result["positive_pct"] == 50
    assert result["negative_pct"] == 25
    assert result["article_count"] == 4


def test_composite_sentiment_is_bounded() -> None:
    result = compute_sentiment(
        [{"sentiment": "positive"}] * 5,
        {
            "rsi_14": 50,
            "macd_histogram": 2,
            "trend_signal": "uptrend",
            "atr_14": 0.5,
            "price": 100,
        },
    )

    assert result["composite_score"] == 90
    assert result["composite_label"] == "positive"
    assert 0 <= result["composite_score"] <= 100


def test_sentiment_modifier_is_limited_to_three_percent_at_extremes() -> None:
    assert _sentiment_modifier_pct({"composite_score": 80}) == 3.0
    assert _sentiment_modifier_pct({"composite_score": 79}) == 0.0
    assert _sentiment_modifier_pct({"composite_score": 21}) == 0.0
    assert _sentiment_modifier_pct({"composite_score": 20}) == -3.0
    assert _sentiment_modifier_pct({}) == 0.0
