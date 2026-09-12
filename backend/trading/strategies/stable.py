"""
Stable Strategy — Mean Reversion + Value Filter

Entry logic (ALL conditions must be met):
  1. Fundamental score >= 65 (from recommender — only quality stocks)
  2. RSI(14) < 32  (oversold, not a falling knife)
  3. Price touches or breaches Bollinger Band lower (20,2)
  4. Price > SMA200  (long-term uptrend — we're not catching a falling knife)
  5. LLM signal is NOT "bearish"
  6. Not already holding this ticker

Exit logic (first condition wins):
  A. RSI > 55  → take profit (momentum restored)
  B. Price > BB mid (SMA20) → take profit
  C. Hard stop-loss: -5% from entry
  D. Time stop: close after 15 trading days if neither A/B triggered

Historical target metrics: Win% ~62%, Profit Factor ~1.8, Max DD ~7%
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from backend.trading.strategies.base import ExitSignal, Signal, StrategyBase


class StableStrategy(StrategyBase):
    strategy_id   = "stable"
    display_name  = "稳健型 Stable (Mean-Reversion)"
    risk_profile  = "stable"
    expected_win_rate  = 0.62
    expected_win_pct   = 0.07
    expected_loss_pct  = 0.05

    # --- Tuneable parameters ---
    RSI_ENTRY       = 32
    RSI_EXIT        = 55
    BB_PERIOD       = 20
    BB_STD          = 2.0
    SMA_TREND       = 200
    MIN_FUND_SCORE  = 65.0
    STOP_LOSS_PCT   = 0.05
    MAX_HOLD_DAYS   = 15

    def __init__(self, **overrides):
        """Allow per-instance parameter overrides for tuning/backtesting.

        Example: StableStrategy(rsi_entry=30, stop_loss_pct=0.06)
        """
        for key, value in overrides.items():
            attr = key.upper()
            if hasattr(self, attr):
                setattr(self, attr, float(value) if isinstance(value, (int, float)) else value)

    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["rsi"]      = self._rsi(df["Close"])
        bb_up, bb_mid, bb_low = self._bollinger(df["Close"], self.BB_PERIOD, self.BB_STD)
        df["bb_upper"] = bb_up
        df["bb_mid"]   = bb_mid
        df["bb_lower"] = bb_low
        df["sma200"]   = df["Close"].rolling(200).mean()
        df["atr"]      = self._atr(df)
        return df

    def diagnose_entry(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[str]:
        skip, _row = self._entry_skip(ticker, df, fundamental_score, llm_signal, current_positions)
        return skip

    def _entry_skip(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ):
        if df is None or len(df) < 210:
            return "history_short", None
        work = self.populate_indicators(df)
        row = work.iloc[-1]
        if any(p.get("symbol") == ticker for p in current_positions):
            return "already_held", row
        if fundamental_score < self.MIN_FUND_SCORE:
            return "fund_lt_65", row
        if llm_signal and llm_signal.lower() == "bearish":
            return "llm_bearish", row
        close = float(row["Close"])
        rsi = float(row["rsi"])
        if pd.isna(rsi) or rsi >= self.RSI_ENTRY:
            return "rsi_not_oversold", row
        bb_lower = float(row["bb_lower"])
        if pd.isna(bb_lower) or close > bb_lower * 1.005:
            return "bb_not_low", row
        sma200 = float(row["sma200"])
        if pd.isna(sma200) or close < sma200 * 0.97:
            return "below_sma200", row
        return None, row

    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[Signal]:
        skip, row = self._entry_skip(ticker, df, fundamental_score, llm_signal, current_positions)
        if skip or row is None:
            return None

        close = float(row["Close"])
        rsi = float(row["rsi"])
        bb_lower = float(row["bb_lower"])
        bb_mid = float(row["bb_mid"])
        sma200 = float(row["sma200"])
        stop   = round(close * (1 - self.STOP_LOSS_PCT), 4)
        target = round(bb_mid, 4)                            # target = BB mid (SMA20)

        reason = (
            f"RSI={rsi:.1f} oversold, price at BB-lower={bb_lower:.2f}, "
            f"above SMA200={sma200:.2f}, fund_score={fundamental_score:.0f}"
        )

        return Signal(
            ticker=ticker,
            side="buy",
            strategy_id=self.strategy_id,
            confidence=round(min(1.0, (self.RSI_ENTRY - rsi) / self.RSI_ENTRY + 0.4), 3),
            reason=reason,
            entry_price=round(close, 4),
            stop_loss_price=stop,
            take_profit_price=target,
            avg_win_pct=self.expected_win_pct,
            avg_loss_pct=self.expected_loss_pct,
            meta={"rsi": rsi, "bb_lower": bb_lower, "bb_mid": bb_mid, "sma200": sma200},
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
        if len(df) < 25:
            return None

        df = self.populate_indicators(df)
        row = df.iloc[-1]
        rsi     = float(row["rsi"])   if not pd.isna(row["rsi"])    else 50.0
        bb_mid  = float(row["bb_mid"]) if not pd.isna(row["bb_mid"]) else current_price

        # A. Hard stop-loss
        if current_price <= stop_loss_price:
            return ExitSignal(ticker=ticker, reason="stop_loss", exit_price=current_price)

        # B. RSI restored
        if rsi >= self.RSI_EXIT:
            return ExitSignal(ticker=ticker, reason="take_profit_rsi", exit_price=current_price)

        # C. Price back above BB mid
        if current_price >= bb_mid:
            return ExitSignal(ticker=ticker, reason="take_profit_bb_mid", exit_price=current_price)

        return None
