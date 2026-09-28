"""Strategy package — Trend, Adaptive, Breakout, Reversion, Research-list, Defensive GLD."""
from backend.trading.strategies.base import StrategyBase, Signal, ExitSignal
from backend.trading.strategies.stable import StableStrategy
from backend.trading.strategies.aggressive import AggressiveStrategy
from backend.trading.strategies.research_list import ResearchListStrategy
from backend.trading.strategies.defensive_gld import DefensiveGldStrategy
from backend.trading.strategies.trend import TrendStrategy
from backend.trading.strategies.adaptive import AdaptiveStrategy
from backend.trading.strategies.registry import KNOWN_STRATEGIES, get_strategy, STRATEGY_REGISTRY

__all__ = [
    "StrategyBase", "Signal", "ExitSignal",
    "StableStrategy", "AggressiveStrategy", "ResearchListStrategy",
    "DefensiveGldStrategy",
    "TrendStrategy", "AdaptiveStrategy",
    "KNOWN_STRATEGIES", "get_strategy", "STRATEGY_REGISTRY",
]
