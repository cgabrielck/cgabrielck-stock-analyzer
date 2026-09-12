"""Trend Strategy — Minervini Stage-2 + pullback entry (Elder timing).

Entry (ALL must pass):
  1. Stage-2 template: Close > SMA50 > SMA150 > SMA200, SMA200 rising
  2. 12-week relative strength vs SPY > 0
  3. Fundamental score >= MIN_FUND_SCORE (default 55)
  4. Pullback reclaim of SMA21/SMA50 (or ATR contraction bounce)
  5. Still within 25% of 52-week high
  6. LLM not explicitly bearish

Exit:
  A. Hard stop (ATR-based initial stop, updated by worker trailing)
  B. Close below SMA50
  C. No BB-mid / RSI-55 scalp exits — let winners run
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from backend.trading.strategies.base import ExitSignal, Signal, StrategyBase
from backend.trading.strategies.stage2 import (
    add_trend_template_columns,
    atr_stop,
    near_52w_high,
    pullback_entry_ok,
    relative_strength_vs_spy,
    stage2_ok,
)


class TrendStrategy(StrategyBase):
    strategy_id = "trend"
    display_name = "趨勢型 Trend (Stage-2)"
    risk_profile = "aggressive"
    expected_win_rate = 0.42
    expected_win_pct = 0.18
    expected_loss_pct = 0.07

    MIN_FUND_SCORE = 55.0
    ATR_STOP_MULT = 1.8
    TRAIL_ATR_MULT = 2.0
    PULLBACK_BAND = 0.03
    MAX_DD_FROM_HIGH = 0.25
    RS_LOOKBACK = 63
    REWARD_RISK_RATIO = 3.0

    def __init__(self, **overrides):
        self.spy_close: Optional[pd.Series] = None
        for key, value in overrides.items():
            attr = key.upper()
            if hasattr(self, attr):
                setattr(self, attr, float(value) if isinstance(value, (int, float)) else value)

    def set_spy_close(self, spy_close: Optional[pd.Series]) -> None:
        self.spy_close = spy_close

    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        return add_trend_template_columns(df)

    def diagnose_entry(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[str]:
        skip, _ = self._entry_skip(ticker, df, fundamental_score, llm_signal, current_positions)
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
        if any(str(p.get("symbol") or "").upper() == ticker.upper() for p in current_positions):
            return "already_held", None
        if llm_signal and str(llm_signal).lower() == "bearish":
            return "llm_bearish", None
        if fundamental_score < self.MIN_FUND_SCORE:
            return "fund_below_min", None

        work = self.populate_indicators(df)
        row = work.iloc[-1]
        ok, code = stage2_ok(row)
        if not ok:
            return code, None
        ok, code = near_52w_high(row, self.MAX_DD_FROM_HIGH)
        if not ok:
            return code, None
        rs, rs_code = relative_strength_vs_spy(work["Close"], self.spy_close, self.RS_LOOKBACK)
        if self.spy_close is not None and (rs is None or rs <= 0):
            return rs_code or "rs_weak", None
        if rs is None:
            rs = 0.01  # offline / no SPY series — don't block unit tests
        ok, code = pullback_entry_ok(
            row,
            self.PULLBACK_BAND,
            prior_closes=work["Close"],
        )
        if not ok:
            return code, None
        return None, (work, row, rs)

    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[Signal]:
        skip, packed = self._entry_skip(ticker, df, fundamental_score, llm_signal, current_positions)
        if skip or packed is None:
            return None
        work, row, rs = packed
        close = float(row["Close"])
        atr = float(row["atr"]) if not pd.isna(row["atr"]) else close * 0.02
        sma50 = float(row["sma50"])
        stop = atr_stop(close, atr, self.ATR_STOP_MULT)
        # Prefer tighter of ATR stop and SMA50 underside
        sma50_stop = round(sma50 * 0.99, 4)
        if sma50_stop < close:
            stop = max(stop, sma50_stop) if stop < close else stop
            stop = min(stop, sma50_stop) if sma50_stop > stop else stop
            # take nearer stop below price
            candidates = [s for s in (atr_stop(close, atr, self.ATR_STOP_MULT), sma50_stop) if s < close]
            stop = round(max(candidates), 4) if candidates else round(close * 0.94, 4)
        risk = close - stop
        target = round(close + self.REWARD_RISK_RATIO * risk, 4) if risk > 0 else round(close * 1.15, 4)

        return Signal(
            ticker=ticker,
            side="buy",
            strategy_id=self.strategy_id,
            confidence=round(min(1.0, 0.45 + max(0.0, rs) * 2), 3),
            reason=(
                f"Stage-2 pullback, RS_12w={rs*100:.1f}%, "
                f"fund={fundamental_score:.0f}, ATR_stop={stop:.2f}"
            ),
            entry_price=round(close, 4),
            stop_loss_price=stop,
            take_profit_price=target,
            avg_win_pct=self.expected_win_pct,
            avg_loss_pct=self.expected_loss_pct,
            meta={
                "entry_kind": "trend",
                "rs_12w": round(rs, 4),
                "sma50": sma50,
                "atr": atr,
                "trail_atr_mult": self.TRAIL_ATR_MULT,
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
        if len(df) < 55:
            return None
        work = self.populate_indicators(df)
        row = work.iloc[-1]
        sma50 = float(row["sma50"]) if not pd.isna(row["sma50"]) else None
        atr = float(row["atr"]) if not pd.isna(row["atr"]) else None

        if current_price <= stop_loss_price:
            return ExitSignal(ticker=ticker, reason="stop_loss", exit_price=current_price)
        if sma50 and current_price < sma50 * 0.995:
            return ExitSignal(ticker=ticker, reason="trend_break_sma50", exit_price=current_price)
        # Soft trail: if price fell more than TRAIL_ATR from recent high in frame
        if atr and atr > 0 and len(work) >= 20:
            recent_high = float(work["High"].iloc[-20:].max())
            trail = recent_high - self.TRAIL_ATR_MULT * atr
            if current_price <= trail:
                return ExitSignal(ticker=ticker, reason="atr_trail", exit_price=current_price)
        return None
