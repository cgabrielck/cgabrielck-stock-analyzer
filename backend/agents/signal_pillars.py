from typing import Any, Dict, Optional


PILLAR_WEIGHTS = {
    "trend": 0.25,
    "momentum": 0.20,
    "mean_reversion": 0.15,
    "volume": 0.20,
    "risk": 0.20,
}


def calculate_five_pillar_score(
    technical: Dict[str, Any], risk_metrics: Optional[Dict[str, Any]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Return an auditable timing score; a high score favors a long entry."""
    active_weights = weights or PILLAR_WEIGHTS
    if set(active_weights) != set(PILLAR_WEIGHTS) or any(value <= 0 for value in active_weights.values()):
        raise ValueError("pillar weights must contain five positive values")
    risk = risk_metrics or technical.get("risk_metrics", {})
    pillars = {
        "trend": _trend_score(technical),
        "momentum": _momentum_score(technical),
        "mean_reversion": _mean_reversion_score(technical),
        "volume": _volume_score(technical),
        "risk": _risk_score(technical, risk),
    }
    available_weight = sum(
        active_weights[name] for name, value in pillars.items() if value["available"]
    )
    if available_weight == 0:
        return {"score": None, "coverage": 0.0, "pillars": pillars, "version": 1}
    score = sum(
        value["score"] * active_weights[name]
        for name, value in pillars.items() if value["available"]
    ) / available_weight
    return {
        "score": round(max(0.0, min(100.0, score)), 1),
        "coverage": round(available_weight, 2),
        "pillars": pillars,
        "weights": active_weights,
        "version": 1,
    }


def _result(score: float, reasons: list[str], available: bool = True) -> Dict[str, Any]:
    return {
        "score": round(max(0.0, min(100.0, score)), 1),
        "available": available,
        "reasons": reasons,
    }


def _trend_score(data: Dict[str, Any]) -> Dict[str, Any]:
    price, sma20, sma50 = data.get("price"), data.get("sma_20"), data.get("sma_50")
    sma200, adx = data.get("sma_200"), data.get("adx_14")
    if not price or not sma50:
        return _result(50, ["price_or_sma50_missing"], False)
    score, reasons = 50.0, []
    score += 15 if price > sma50 else -15
    reasons.append("price_above_sma50" if price > sma50 else "price_below_sma50")
    if sma20:
        score += 12 if sma20 > sma50 else -12
        reasons.append("sma20_above_sma50" if sma20 > sma50 else "sma20_below_sma50")
    if sma200:
        score += 13 if price > sma200 else -13
        reasons.append("price_above_sma200" if price > sma200 else "price_below_sma200")
    if adx is not None and adx >= 25:
        direction = 1 if price > sma50 else -1
        score += 10 * direction
        reasons.append("strong_trend")
    return _result(score, reasons)


def _momentum_score(data: Dict[str, Any]) -> Dict[str, Any]:
    rsi, macd = data.get("rsi_14"), data.get("macd_histogram")
    if rsi is None and macd is None:
        return _result(50, ["momentum_missing"], False)
    score, reasons = 50.0, []
    if rsi is not None:
        if 50 <= rsi <= 65:
            score += 25
            reasons.append("rsi_bullish")
        elif 40 <= rsi < 50:
            score += 10
            reasons.append("rsi_recovering")
        elif rsi > 75:
            score -= 20
            reasons.append("rsi_extreme_overbought")
        elif rsi < 30:
            score -= 10
            reasons.append("rsi_weak")
    if macd is not None:
        score += 20 if macd > 0 else -20
        reasons.append("macd_positive" if macd > 0 else "macd_negative")
    return _result(score, reasons)


def _mean_reversion_score(data: Dict[str, Any]) -> Dict[str, Any]:
    price, lower, upper = data.get("price"), data.get("bb_lower"), data.get("bb_upper")
    rsi = data.get("rsi_14")
    if not price or not lower or not upper or upper <= lower:
        return _result(50, ["bollinger_missing"], False)
    position = (price - lower) / (upper - lower)
    score, reasons = 50.0, []
    if position <= 0 and rsi is not None and rsi <= 35:
        score, reasons = 80, ["oversold_at_lower_band"]
    elif position < 0.25:
        score, reasons = 68, ["near_lower_band"]
    elif position > 1 and rsi is not None and rsi >= 65:
        score, reasons = 20, ["overbought_above_upper_band"]
    elif position > 0.8:
        score, reasons = 38, ["near_upper_band"]
    else:
        reasons = ["inside_normal_band"]
    return _result(score, reasons)


def _volume_score(data: Dict[str, Any]) -> Dict[str, Any]:
    ratio = data.get("dollar_volume_ratio")
    if ratio is None:
        ratio = data.get("volume_ratio_10_50")
    quality = data.get("volume_quality_score")
    obv_trend = data.get("obv_trend")
    if ratio is None and quality is None:
        return _result(50, ["volume_missing"], False)
    score, reasons = 50.0, []
    if ratio is not None:
        if ratio >= 1.5:
            score += 15
            reasons.append("strong_participation")
        elif ratio >= 1.0:
            score += 8
            reasons.append("healthy_participation")
        elif ratio < 0.6:
            score -= 12
            reasons.append("weak_participation")
    if quality is not None:
        score += (float(quality) - 50) * 0.4
        reasons.append("positive_price_volume" if quality >= 50 else "negative_price_volume")
    if obv_trend is not None:
        score += 8 if obv_trend > 0 else -8
        reasons.append("obv_rising" if obv_trend > 0 else "obv_falling")
    return _result(score, reasons)


def _risk_score(data: Dict[str, Any], risk: Dict[str, Any]) -> Dict[str, Any]:
    atr, price = data.get("atr_14"), data.get("price")
    available = bool(risk.get("available")) or (atr is not None and price)
    if not available:
        return _result(50, ["risk_missing"], False)
    score, reasons = 70.0, []
    volatility = risk.get("annual_volatility_pct")
    drawdown = risk.get("max_drawdown_pct")
    beta = risk.get("beta")
    if volatility is not None:
        score += 10 if volatility < 20 else -10 if volatility > 45 else 0
        reasons.append("low_volatility" if volatility < 20 else "high_volatility" if volatility > 45 else "normal_volatility")
    if drawdown is not None:
        score -= 20 if abs(drawdown) > 35 else 8 if abs(drawdown) > 20 else 0
        reasons.append("drawdown_checked")
    if beta is not None and beta > 1.5:
        score -= 10
        reasons.append("high_beta")
    if atr is not None and price:
        atr_pct = atr / price * 100
        score -= 15 if atr_pct > 5 else 5 if atr_pct > 3 else 0
        reasons.append("atr_checked")
    return _result(score, reasons)
