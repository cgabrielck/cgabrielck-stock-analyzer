from typing import Any, Dict, List, Optional, Tuple

from agents.score_contract import QUALITY_EXPORT_KEYS
from utils.constants import SCORING_WEIGHTS

# STRATEGY_ADOPTION P4: quality (ROE / margins / leverage) split out of the growth stew.
_QUALITY_PILLARS = {
    "roe": "quality_roe_score",
    "profit_margin": "quality_margin_score",
    "debt_equity": "quality_leverage_score",
}
_GROUP_BY_KEY = {
    "revenue_growth": "growth",
    "eps_growth": "growth",
    "profit_margin": "quality",
    "roe": "quality",
    "debt_equity": "quality",
    "peg_ratio": "value",
}


def _clip_pct(raw: float, scale: float) -> float:
    return min(max((raw / scale) * 100, 0), 100)


def _peg_subscore(peg: float) -> float:
    if peg <= 0.5:
        return 100
    if peg <= 1.0:
        return 90
    if peg <= 1.5:
        return 75
    if peg <= 2.0:
        return 60
    if peg <= 3.0:
        return 40
    if peg <= 5.0:
        return 20
    return 10


def _debt_subscore(debt_equity: float) -> float:
    if debt_equity <= 0.3:
        return 100
    if debt_equity <= 0.5:
        return 85
    if debt_equity <= 1.0:
        return 65
    if debt_equity <= 2.0:
        return 40
    if debt_equity <= 3.0:
        return 20
    return 10


def _collect_metric_rows(
    data: Dict[str, Any],
    weights: Dict[str, float],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, str]], int]:
    """Same heuristic as the historic growth scorer; used for P4 attribution."""
    rows: List[Dict[str, Any]] = []
    details: Dict[str, Dict[str, str]] = {}
    metrics_used = 0

    if data.get("revenue_growth") is not None:
        raw = data["revenue_growth"]
        s = _clip_pct(raw, 20.0)
        rows.append({"key": "revenue_growth", "score": s, "weight": weights.get("revenue_growth", 0)})
        details["营收增长 (YoY)"] = {"value": f"{raw:.1f}%", "score": f"{s:.0f}/100"}
        metrics_used += 1

    if data.get("eps_growth") is not None:
        raw = data["eps_growth"]
        s = _clip_pct(raw, 20.0)
        rows.append({"key": "eps_growth", "score": s, "weight": weights.get("eps_growth", 0)})
        details["EPS增长 (YoY)"] = {"value": f"{raw:.1f}%", "score": f"{s:.0f}/100"}
        metrics_used += 1

    if data.get("profit_margin") is not None:
        raw = data["profit_margin"]
        s = _clip_pct(raw, 20.0)
        rows.append({"key": "profit_margin", "score": s, "weight": weights.get("profit_margin", 0)})
        details["净利润率"] = {"value": f"{raw:.1f}%", "score": f"{s:.0f}/100"}
        metrics_used += 1

    if data.get("peg") is not None and data["peg"] > 0:
        p = data["peg"]
        s = _peg_subscore(p)
        rows.append({"key": "peg_ratio", "score": s, "weight": weights.get("peg_ratio", 0)})
        details["PEG比率"] = {"value": f"{p:.2f}", "score": f"{s:.0f}/100"}
        metrics_used += 1

    if data.get("roe") is not None:
        raw = data["roe"]
        s = _clip_pct(raw, 30.0)
        rows.append({"key": "roe", "score": s, "weight": weights.get("roe", 0)})
        details["ROE"] = {"value": f"{raw:.1f}%", "score": f"{s:.0f}/100"}
        metrics_used += 1

    if data.get("debt_equity") is not None:
        d = data["debt_equity"]
        s = _debt_subscore(d)
        rows.append({"key": "debt_equity", "score": s, "weight": weights.get("debt_equity", 0)})
        details["负债/权益比"] = {"value": f"{d:.2f}", "score": f"{s:.0f}/100"}
        metrics_used += 1

    return rows, details, metrics_used


def _group_score(rows: List[Dict[str, Any]], group: str) -> Optional[float]:
    points = 0.0
    max_possible = 0.0
    for row in rows:
        if _GROUP_BY_KEY.get(row["key"]) != group:
            continue
        points += row["score"] * row["weight"]
        max_possible += row["weight"] * 100
    if max_possible <= 0:
        return None
    return round(points / max_possible * 100, 1)


def score_fundamentals(
    data: Dict[str, Any],
    custom_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Composite growth score plus P4 quality split. Does not change the 0–100 total."""
    w = custom_weights if custom_weights is not None else SCORING_WEIGHTS
    rows, details, metrics_used = _collect_metric_rows(data, w)

    points = sum(row["score"] * row["weight"] for row in rows)
    max_possible = sum(row["weight"] * 100 for row in rows)
    growth_score = round((points / max_possible) * 100, 1) if max_possible > 0 else 0.0

    pillar_scores = {export_key: None for export_key in _QUALITY_PILLARS.values()}
    group_points = {"quality": 0.0, "growth": 0.0, "value": 0.0}
    for row in rows:
        export_key = _QUALITY_PILLARS.get(row["key"])
        if export_key:
            pillar_scores[export_key] = round(row["score"], 1)
        group = _GROUP_BY_KEY.get(row["key"])
        if group:
            group_points[group] += row["score"] * row["weight"]

    attribution = {
        "quality_weighted_points": round(group_points["quality"], 4),
        "growth_weighted_points": round(group_points["growth"], 4),
        "value_weighted_points": round(group_points["value"], 4),
        "max_possible": round(max_possible, 4),
        "composite_share_quality": round(group_points["quality"] / max_possible, 4) if max_possible else None,
        "composite_share_growth": round(group_points["growth"] / max_possible, 4) if max_possible else None,
        "composite_share_value": round(group_points["value"] / max_possible, 4) if max_possible else None,
    }

    return {
        "growth_score": growth_score,
        "score_details": details,
        "metrics_used": metrics_used,
        "quality_score": _group_score(rows, "quality"),
        "quality_roe_score": pillar_scores["quality_roe_score"],
        "quality_margin_score": pillar_scores["quality_margin_score"],
        "quality_leverage_score": pillar_scores["quality_leverage_score"],
        "growth_component_score": _group_score(rows, "growth"),
        "value_component_score": _group_score(rows, "value"),
        "score_attribution": attribution,
    }


def calculate_growth_score(
    data: Dict[str, Any],
    custom_weights: Optional[Dict[str, float]] = None,
) -> Tuple[float, Dict[str, Dict[str, str]], int]:
    result = score_fundamentals(data, custom_weights)
    return result["growth_score"], result["score_details"], result["metrics_used"]


def quality_attribution(
    data: Dict[str, Any],
    custom_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """ROE / margins / leverage columns for Scan + factor IC (STRATEGY_ADOPTION P4)."""
    result = score_fundamentals(data, custom_weights)
    out = {key: result.get(key) for key in QUALITY_EXPORT_KEYS}
    out["score_attribution"] = result["score_attribution"]
    return out


def calculate_all_scores(
    all_data: Dict[str, Dict[str, Any]],
    custom_weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for ticker, data in all_data.items():
        if "error" in data:
            continue
        if data.get("sector") is None:
            data["sector"] = "Other"
        result = score_fundamentals(data, custom_weights)
        data["growth_score"] = result["growth_score"]
        data["score_details"] = result["score_details"]
        data["total_score"] = result["growth_score"]
        data["metrics_used"] = result["metrics_used"]
        data["score_attribution"] = result["score_attribution"]
        for key in QUALITY_EXPORT_KEYS:
            data[key] = result.get(key)
        scored.append(data)
    scored.sort(key=lambda x: x.get("total_score", 0) or 0, reverse=True)
    return scored
