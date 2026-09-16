"""Shared fund-score contract for Scan ranker and research_list.

Worker / research_list read scores through ``get_fund_score`` (as-of).
The ranker persists the same field on ``last_scan.json`` so both sides agree.
This module never places orders and does not change default paper strategy.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Scan ranker + research_list / paper worker (STRATEGY_ADOPTION §1.1).
SHARED_FUND_SCORE_FIELD = "risk_adjusted_score"
SHARED_FUND_SCORE_FALLBACKS = ("growth_score", "model_score")

QUALITY_EXPORT_KEYS = (
    "quality_score",
    "quality_roe_score",
    "quality_margin_score",
    "quality_leverage_score",
    "growth_component_score",
    "value_component_score",
)


def fund_score_from_row(row: Optional[Dict[str, Any]]) -> Optional[float]:
    """Prefer risk-adjusted (ranker), then growth / model. None if missing."""
    if not row:
        return None
    for key in (SHARED_FUND_SCORE_FIELD, *SHARED_FUND_SCORE_FALLBACKS):
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def quality_fields_from_row(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {key: None for key in QUALITY_EXPORT_KEYS}
    out: Dict[str, Any] = {}
    for key in QUALITY_EXPORT_KEYS:
        value = row.get(key)
        if value is None:
            out[key] = None
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            out[key] = None
    return out
