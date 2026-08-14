"""
Strategy registry — maps strategy_id strings to strategy instances.

Usage:
    from backend.trading.strategies.registry import get_strategy
    strategy = get_strategy("stable")
"""
from __future__ import annotations

from typing import Dict

from backend.trading.strategies.base import StrategyBase
from backend.trading.strategies.stable import StableStrategy
from backend.trading.strategies.aggressive import AggressiveStrategy


class HybridStrategy(StrategyBase):
    """
    Hybrid — runs both Stable and Aggressive and returns whichever fires first.
    Stable signals take priority (lower risk profile).
    """
    strategy_id  = "hybrid"
    display_name = "混合型 Hybrid (Stable + Aggressive)"
    risk_profile = "hybrid"
    expected_win_rate  = 0.54
    expected_win_pct   = 0.10
    expected_loss_pct  = 0.06

    def __init__(self, **overrides):
        stable_overrides = {k: v for k, v in overrides.items() if hasattr(StableStrategy(), k.upper())}
        aggr_overrides = {k: v for k, v in overrides.items() if hasattr(AggressiveStrategy(), k.upper())}
        self._stable     = StableStrategy(**stable_overrides)
        self._aggressive = AggressiveStrategy(**aggr_overrides)

    def populate_indicators(self, df):
        return df  # each sub-strategy does its own

    def generate_signal(self, ticker, df, fundamental_score, llm_signal, current_positions):
        sig = self._stable.generate_signal(ticker, df, fundamental_score, llm_signal, current_positions)
        if sig:
            return sig
        return self._aggressive.generate_signal(ticker, df, fundamental_score, llm_signal, current_positions)

    def check_exit(self, ticker, entry_price, current_price, df, stop_loss_price, take_profit_price):
        exit_sig = self._stable.check_exit(ticker, entry_price, current_price, df, stop_loss_price, take_profit_price)
        if exit_sig:
            return exit_sig
        return self._aggressive.check_exit(ticker, entry_price, current_price, df, stop_loss_price, take_profit_price)


# Registry of all available strategies
_INSTANCES: Dict[str, StrategyBase] = {
    "stable":     StableStrategy(),
    "aggressive": AggressiveStrategy(),
    "hybrid":     HybridStrategy(),
}

STRATEGY_REGISTRY: Dict[str, StrategyBase] = _INSTANCES


def get_strategy(strategy_id: str, **overrides) -> StrategyBase:
    """Return a strategy instance by ID.  Defaults to 'stable' if unknown.

    Args:
        **overrides: Parameter overrides applied to a fresh instance. When
            provided, a new instance is built (the shared singleton is left
            untouched) so backtests can sweep parameters safely.
    """
    if overrides:
        cls_map = {"stable": StableStrategy, "aggressive": AggressiveStrategy, "hybrid": HybridStrategy}
        cls = cls_map.get(strategy_id, StableStrategy)
        return cls(**overrides)
    return _INSTANCES.get(strategy_id, _INSTANCES["stable"])
