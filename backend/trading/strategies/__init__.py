"""Strategy package — Stable, Aggressive, and Hybrid trading strategies."""
from backend.trading.strategies.base import StrategyBase, Signal, ExitSignal
from backend.trading.strategies.stable import StableStrategy
from backend.trading.strategies.aggressive import AggressiveStrategy
from backend.trading.strategies.registry import get_strategy, STRATEGY_REGISTRY

__all__ = [
    "StrategyBase", "Signal", "ExitSignal",
    "StableStrategy", "AggressiveStrategy",
    "get_strategy", "STRATEGY_REGISTRY",
]
