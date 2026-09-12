"""
Abstract base classes for all trading strategies.

Design inspired by Freqtrade's strategy architecture:
  populate_indicators → generate_signal → check_exit
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


@dataclass
class Signal:
    """A buy/sell signal produced by a strategy."""
    ticker: str
    side: str                          # "buy" | "sell"
    strategy_id: str
    confidence: float                  # 0.0 – 1.0  (used for Kelly sizing)
    reason: str                        # human-readable explanation
    entry_price: float                 # suggested limit price
    stop_loss_price: float             # hard stop
    take_profit_price: float           # primary target
    avg_win_pct: float  = 0.08         # historical avg win % for Kelly
    avg_loss_pct: float = 0.04         # historical avg loss % for Kelly
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExitSignal:
    """A signal to exit (close) an existing position."""
    ticker: str
    reason: str                        # "stop_loss" | "take_profit" | "signal_reversal" | "trailing_stop"
    exit_price: float


class StrategyBase(ABC):
    """
    All strategies must inherit from this class.

    Lifecycle per Worker tick:
        1. populate_indicators(df)   → enriched DataFrame
        2. generate_signal(...)      → Optional[Signal]
        3. check_exit(...)           → Optional[ExitSignal]
    """

    #: Short unique identifier — used in the UI dropdown and order metadata
    strategy_id: str = "base"
    #: Display name shown in the UI
    display_name: str = "Base Strategy"
    #: "stable" | "aggressive" | "hybrid"
    risk_profile: str = "stable"
    #: Approximate historical win-rate for Kelly sizing
    expected_win_rate: float = 0.55
    #: Approximate avg win / avg loss ratio for Kelly sizing
    expected_win_pct: float = 0.08
    expected_loss_pct: float = 0.04

    @abstractmethod
    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add strategy-specific columns to the OHLCV DataFrame."""

    @abstractmethod
    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[Signal]:
        """
        Analyse *df* and return a Signal if entry conditions are met.
        Return None if no actionable setup exists.
        """

    def diagnose_entry(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Return a skip_codes id when generate_signal would be None, else None."""
        return "no_setup"

    @abstractmethod
    def check_exit(
        self,
        ticker: str,
        entry_price: float,
        current_price: float,
        df: pd.DataFrame,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> Optional[ExitSignal]:
        """
        Check whether an open position should be closed.
        Return an ExitSignal if exit conditions are met, else None.
        """

    # ------------------------------------------------------------------
    # Shared helpers available to all subclasses
    # ------------------------------------------------------------------

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss.replace(0, float("nan"))
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _bollinger(close: pd.Series, period: int = 20, std_dev: float = 2.0):
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        return sma + std * std_dev, sma, sma - std * std_dev

    @staticmethod
    def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        return macd_line, signal_line, macd_line - signal_line

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, prev_close = df["High"], df["Low"], df["Close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()
