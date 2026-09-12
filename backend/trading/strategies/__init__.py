"""Strategy package — Stable, Aggressive, Hybrid, and Research-list."""
from backend.trading.strategies.base import StrategyBase, Signal, ExitSignal
from backend.trading.strategies.stable import StableStrategy
from backend.trading.strategies.aggressive import AggressiveStrategy
from backend.trading.strategies.research_list import ResearchListStrategy
from backend.trading.strategies.registry import KNOWN_STRATEGIES, get_strategy, STRATEGY_REGISTRY

__all__ = [
    "StrategyBase", "Signal", "ExitSignal",
    "StableStrategy", "AggressiveStrategy", "ResearchListStrategy",
    "KNOWN_STRATEGIES", "get_strategy", "STRATEGY_REGISTRY",
]
