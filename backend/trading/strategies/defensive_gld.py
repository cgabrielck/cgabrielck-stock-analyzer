"""Named defensive rotation: buy GLD only in RED / PANIC regimes.

Operator must select ``defensive_gld`` in AI Mode. Never the desk default.
LLM never orders. Worker still sizes through Kelly + RiskEngine + mandate.
Stage-2 (Minervini uptrend) is not applicable to a gold hedge.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from backend.trading.strategies.base import ExitSignal, Signal, StrategyBase

DEFENSIVE_TICKER = "GLD"
RED_REGIMES = frozenset({"bear", "red"})
PANIC_REGIMES = frozenset({"high_volatility", "panic"})
GREEN_REGIMES = frozenset({"bull", "green"})


def defensive_bucket(regime_name: Optional[str]) -> str:
    """Map Cgab SPY+VIX regimes onto CYDMDM-style buckets."""
    name = str(regime_name or "").strip().lower()
    if name in PANIC_REGIMES:
        return "PANIC"
    if name in RED_REGIMES:
        return "RED"
    if name in GREEN_REGIMES:
        return "GREEN"
    return "YELLOW"


class DefensiveGldStrategy(StrategyBase):
    strategy_id = "defensive_gld"
    display_name = "防禦 GLD (RED/PANIC)"
    risk_profile = "stable"
    expected_win_rate = 0.52
    expected_win_pct = 0.06
    expected_loss_pct = 0.04
    universe_override = (DEFENSIVE_TICKER,)

    STOP_LOSS_PCT = 0.05
    TAKE_PROFIT_PCT = 0.08
    PANIC_CONFIDENCE = 0.70
    RED_CONFIDENCE = 0.55

    def __init__(self, **overrides):
        self._regime: Dict[str, Any] = {}
        for key, value in overrides.items():
            attr = key.upper()
            if hasattr(self, attr):
                setattr(self, attr, float(value) if isinstance(value, (int, float)) else value)

    def set_regime(self, regime: Optional[Dict[str, Any]]) -> None:
        self._regime = dict(regime or {})

    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None:
            return pd.DataFrame()
        return df.copy()

    def _bucket(self) -> str:
        return defensive_bucket((self._regime or {}).get("regime"))

    def diagnose_entry(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[str]:
        if str(ticker or "").upper() != DEFENSIVE_TICKER:
            return "not_defensive_asset"
        if any(str(p.get("symbol") or "").upper() == DEFENSIVE_TICKER for p in current_positions):
            return "already_held"
        if llm_signal and str(llm_signal).lower() == "bearish":
            return "llm_bearish"
        if self._bucket() not in ("RED", "PANIC"):
            return "not_defensive_regime"
        if df is None or len(df) < 30:
            return "history_short"
        return None

    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[Signal]:
        if self.diagnose_entry(ticker, df, fundamental_score, llm_signal, current_positions):
            return None
        close = float(df["Close"].iloc[-1])
        stop = round(close * (1 - float(self.STOP_LOSS_PCT)), 4)
        target = round(close * (1 + float(self.TAKE_PROFIT_PCT)), 4)
        bucket = self._bucket()
        confidence = self.PANIC_CONFIDENCE if bucket == "PANIC" else self.RED_CONFIDENCE
        return Signal(
            ticker=DEFENSIVE_TICKER,
            side="buy",
            strategy_id=self.strategy_id,
            confidence=round(float(confidence), 3),
            reason=f"Defensive GLD rotation ({bucket}) — Kelly/RiskEngine still size",
            entry_price=round(close, 4),
            stop_loss_price=stop,
            take_profit_price=target,
            avg_win_pct=self.expected_win_pct,
            avg_loss_pct=self.expected_loss_pct,
            meta={
                "entry_kind": "defensive_gld",
                "regime_bucket": bucket,
                "regime": (self._regime or {}).get("regime"),
            },
        )

    def check_exit(
        self,
        ticker: str,
        entry_price: float,
        current_price: float,
        df: pd.DataFrame,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> Optional[ExitSignal]:
        if current_price <= stop_loss_price:
            return ExitSignal(ticker=ticker, reason="stop_loss", exit_price=current_price)
        if current_price >= take_profit_price:
            return ExitSignal(ticker=ticker, reason="take_profit", exit_price=current_price)
        if str(ticker or "").upper() == DEFENSIVE_TICKER and self._bucket() == "GREEN":
            return ExitSignal(ticker=ticker, reason="signal_reversal", exit_price=current_price)
        return None
