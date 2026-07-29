from typing import Any, Dict, List, Optional


def aggregate_news_sentiment(news_articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not news_articles:
        return {
            "score": 50,
            "label": "neutral",
            "positive_pct": 0,
            "negative_pct": 0,
            "article_count": 0,
        }
    pos = sum(1 for n in news_articles if n.get("sentiment") == "positive")
    neg = sum(1 for n in news_articles if n.get("sentiment") == "negative")
    total = len(news_articles)
    net = (pos - neg) / total
    score = max(0, min(100, round(50 + net * 50)))
    label = "positive" if score >= 60 else "negative" if score <= 40 else "neutral"
    return {
        "score": score,
        "label": label,
        "positive_pct": round(pos / total * 100),
        "negative_pct": round(neg / total * 100),
        "article_count": total,
    }


def _momentum_sentiment(rsi: Optional[float], macd_hist: Optional[float], trend: Optional[str]) -> Dict[str, Any]:
    score = 50
    if rsi is not None:
        if 40 <= rsi <= 60:
            score += 15
        elif rsi > 70:
            score -= 15
        elif rsi < 30:
            score += 10
    if macd_hist is not None:
        score += 10 if macd_hist > 0 else -10
    if trend == "uptrend":
        score += 10
    elif trend == "downtrend":
        score -= 10
    score = max(0, min(100, score))
    label = "positive" if score >= 60 else "negative" if score <= 40 else "neutral"
    return {"score": score, "label": label}


def _volatility_sentiment(atr: Optional[float], price: Optional[float]) -> Dict[str, Any]:
    if atr is None or price is None or price <= 0:
        return {"score": 50, "label": "neutral"}
    vol_pct = atr / price * 100
    if vol_pct < 1.0:
        score = 80
        label = "positive"
    elif vol_pct < 2.5:
        score = 60
        label = "positive"
    elif vol_pct < 4.0:
        score = 40
        label = "negative"
    else:
        score = 20
        label = "negative"
    return {"score": score, "label": label}


def compute_sentiment(
    news_articles: List[Dict[str, Any]],
    technical_data: Dict[str, Any],
) -> Dict[str, Any]:
    news = aggregate_news_sentiment(news_articles)
    momentum = _momentum_sentiment(
        technical_data.get("rsi_14"),
        technical_data.get("macd_histogram"),
        technical_data.get("trend_signal"),
    )
    volatility = _volatility_sentiment(
        technical_data.get("atr_14"),
        technical_data.get("price"),
    )

    composite = round(news["score"] * 0.40 + momentum["score"] * 0.35 + volatility["score"] * 0.25)
    composite = max(0, min(100, composite))
    if composite >= 60:
        composite_label = "positive"
    elif composite <= 40:
        composite_label = "negative"
    else:
        composite_label = "neutral"

    return {
        "composite_score": composite,
        "composite_label": composite_label,
        "dimensions": {
            "news": news,
            "momentum": momentum,
            "volatility": volatility,
        },
    }
