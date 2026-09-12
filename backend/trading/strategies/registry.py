"""
Strategy registry — maps strategy_id strings to strategy instances.

Canonical desk modes: adaptive | trend | breakout | research_list
Legacy aliases: hybrid→adaptive, aggressive→breakout, stable→reversion (same class)
"""
from __future__ import annotations

from typing import Dict

from backend.trading.strategies.adaptive import AdaptiveStrategy
from backend.trading.strategies.aggressive import AggressiveStrategy
from backend.trading.strategies.base import StrategyBase
from backend.trading.strategies.research_list import ResearchListStrategy
from backend.trading.strategies.stable import StableStrategy
from backend.trading.strategies.trend import TrendStrategy

KNOWN_STRATEGIES = (
    "adaptive",
    "trend",
    "breakout",
    "research_list",
    # legacy aliases
    "hybrid",
    "aggressive",
    "stable",
    "reversion",
)


class HybridStrategy(AdaptiveStrategy):
    """Legacy name for AdaptiveStrategy (kept for ai_mode.json / old workers)."""

    strategy_id = "hybrid"
    display_name = "混合型 Hybrid → Adaptive"


# Registry of all available strategies
_INSTANCES: Dict[str, StrategyBase] = {
    "adaptive": AdaptiveStrategy(),
    "trend": TrendStrategy(),
    "breakout": AggressiveStrategy(),
    "aggressive": AggressiveStrategy(),  # alias
    "research_list": ResearchListStrategy(),
    "stable": StableStrategy(),  # Connors reversion
    "reversion": StableStrategy(),
    "hybrid": HybridStrategy(),
}

STRATEGY_REGISTRY: Dict[str, StrategyBase] = _INSTANCES

_CLS_MAP = {
    "adaptive": AdaptiveStrategy,
    "trend": TrendStrategy,
    "breakout": AggressiveStrategy,
    "aggressive": AggressiveStrategy,
    "research_list": ResearchListStrategy,
    "stable": StableStrategy,
    "reversion": StableStrategy,
    "hybrid": HybridStrategy,
}


def get_strategy(strategy_id: str, **overrides) -> StrategyBase:
    """Return a strategy instance by ID. Defaults to 'adaptive' if unknown."""
    sid = (strategy_id or "adaptive").lower()
    if overrides:
        cls = _CLS_MAP.get(sid, AdaptiveStrategy)
        return cls(**overrides)
    return _INSTANCES.get(sid, _INSTANCES["adaptive"])
