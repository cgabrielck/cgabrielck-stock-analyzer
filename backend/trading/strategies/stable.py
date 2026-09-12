"""
Reversion Strategy (id: stable) — Connors-style shallow pullback in Stage-2.

NOT deep RSI14<32 mean-reversion. Entry requires:
  1. Still Stage-2 (Close > SMA50 > SMA150 > SMA200, SMA200 rising)
  2. Fundamental score >= MIN_FUND_SCORE
  3. RSI(2) < RSI2_ENTRY  OR  close within band of SMA21 after a dip
  4. LLM not bearish

Exit: tight stop, RSI2 restore or SMA21 reclaim target — small wins, cut fast.
Legacy id remains ``stable``; ``reversion`` is an alias in the registry.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from backend.trading.strategies.base import ExitSignal, Signal, StrategyBase
from backend.trading.strategies.stage2 import add_trend_template_columns, stage2_ok


class StableStrategy(StrategyBase):
    strategy_id = "stable"
    display_name = "回歸型 Reversion (Connors / Stage-2)"
    risk_profile = "stable"
    expected_win_rate = 0.58
    expected_win_pct = 0.05
    expected_loss_pct = 0.035

    RSI2_ENTRY = 5.0
    RSI2_EXIT = 65.0
    SMA_PULLBACK = 21
    PULLBACK_BAND = 0.015
    MIN_FUND_SCORE = 60.0
    STOP_LOSS_PCT = 0.035
    MAX_HOLD_DAYS = 8
    # Legacy knobs kept so old overrides / tests don't explode
    RSI_ENTRY = 32
    RSI_EXIT = 55
    BB_PERIOD = 20
    BB_STD = 2.0
    SMA_TREND = 200

    def __init__(self, **overrides):
        for key, value in overrides.items():
            attr = key.upper()
            if hasattr(self, attr):
                setattr(self, attr, float(value) if isinstance(value, (int, float)) else value)

    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        work = add_trend_template_columns(df)
        work["rsi2"] = self._rsi(work["Close"], period=2)
        work["rsi"] = self._rsi(work["Close"], period=14)
        bb_up, bb_mid, bb_low = self._bollinger(work["Close"], self.BB_PERIOD, self.BB_STD)
        work["bb_upper"] = bb_up
        work["bb_mid"] = bb_mid
        work["bb_lower"] = bb_low
        return work

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
            return "fund_lt_65" if self.MIN_FUND_SCORE >= 65 else "fund_below_min", row
        if llm_signal and llm_signal.lower() == "bearish":
            return "llm_bearish", row
        ok, code = stage2_ok(row)
        if not ok:
            return code or "not_stage2", row

        close = float(row["Close"])
        rsi2 = float(row["rsi2"]) if not pd.isna(row["rsi2"]) else 50.0
        sma21 = float(row["sma21"]) if not pd.isna(row["sma21"]) else close
        dipped = rsi2 < self.RSI2_ENTRY
        near_sma = abs(close - sma21) / sma21 <= self.PULLBACK_BAND and close <= sma21 * 1.01
        # Prefer a green reclaim day when using SMA pullback
        prev_close = float(work["Close"].iloc[-2]) if len(work) > 1 else close
        reclaim = close >= prev_close
        if not (dipped or (near_sma and reclaim)):
            return "rsi_not_oversold", row
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
        rsi2 = float(row["rsi2"]) if not pd.isna(row["rsi2"]) else 50.0
        sma21 = float(row["sma21"]) if not pd.isna(row["sma21"]) else close
        stop = round(close * (1 - self.STOP_LOSS_PCT), 4)
        target = round(max(sma21, close * 1.025), 4)

        reason = (
            f"Connors pullback RSI2={rsi2:.1f}, near SMA21={sma21:.2f}, "
            f"Stage-2 OK, fund={fundamental_score:.0f}"
        )
        return Signal(
            ticker=ticker,
            side="buy",
            strategy_id=self.strategy_id,
            confidence=round(min(1.0, 0.4 + (self.RSI2_ENTRY - min(rsi2, self.RSI2_ENTRY)) / 20), 3),
            reason=reason,
            entry_price=round(close, 4),
            stop_loss_price=stop,
            take_profit_price=target,
            avg_win_pct=self.expected_win_pct,
            avg_loss_pct=self.expected_loss_pct,
            meta={"entry_kind": "reversion", "rsi2": rsi2, "sma21": sma21},
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
        work = self.populate_indicators(df)
        row = work.iloc[-1]
        rsi2 = float(row["rsi2"]) if not pd.isna(row["rsi2"]) else 50.0
        sma21 = float(row["sma21"]) if not pd.isna(row["sma21"]) else current_price

        if current_price <= stop_loss_price:
            return ExitSignal(ticker=ticker, reason="stop_loss", exit_price=current_price)
        if rsi2 >= self.RSI2_EXIT:
            return ExitSignal(ticker=ticker, reason="take_profit_rsi2", exit_price=current_price)
        if current_price >= take_profit_price or current_price >= sma21 * 1.01:
            return ExitSignal(ticker=ticker, reason="take_profit_sma21", exit_price=current_price)
        return None


# Backward-compatible alias name
ReversionStrategy = StableStrategy
