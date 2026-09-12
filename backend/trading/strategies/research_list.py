"""Buy the latest Scan top-N through RiskEngine — not a silent rewrite of Stable."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from backend.trading.strategies.base import ExitSignal, Signal, StrategyBase


class ResearchListStrategy(StrategyBase):
    """Risk-gated buys from a *fresh* Scan top-N. Stale scan → no entries."""

    strategy_id = "research_list"
    display_name = "掃描名單 Research list (Scan top-N)"
    risk_profile = "stable"
    expected_win_rate = 0.52
    expected_win_pct = 0.10
    expected_loss_pct = 0.06
    requires_fresh_scan = True

    TOP_N = 5.0
    MIN_FUND_SCORE = 65.0
    STOP_LOSS_PCT = 0.06
    TAKE_PROFIT_PCT = 0.10

    def __init__(self, **overrides):
        for key, value in overrides.items():
            attr = key.upper()
            if hasattr(self, attr):
                setattr(self, attr, float(value) if isinstance(value, (int, float)) else value)

    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.copy()

    def _scan_book(self) -> Tuple[List[str], bool]:
        try:
            from backend.api.research_jobs import latest_scan

            scan = latest_scan()
        except Exception:
            return [], True
        stale = bool(scan.get("stale", True) or not scan.get("available"))
        tickers = [str(t).upper() for t in (scan.get("top5_tickers") or []) if t]
        if not tickers:
            for row in scan.get("recommendations") or []:
                name = str((row or {}).get("ticker") or "").upper()
                if name:
                    tickers.append(name)
        n = max(1, int(self.TOP_N))
        return tickers[:n], stale

    def diagnose_entry(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[str]:
        if any(str(p.get("symbol") or "").upper() == ticker.upper() for p in current_positions):
            return "already_held"
        top, stale = self._scan_book()
        if stale:
            return "research_stale"
        if ticker.upper() not in top:
            return "not_in_scan_list"
        if llm_signal and str(llm_signal).lower() == "bearish":
            return "llm_bearish"
        if fundamental_score < self.MIN_FUND_SCORE:
            return "fund_lt_65"
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
        stop = round(close * (1 - self.STOP_LOSS_PCT), 4)
        target = round(close * (1 + self.TAKE_PROFIT_PCT), 4)
        top, _ = self._scan_book()
        return Signal(
            ticker=ticker,
            side="buy",
            strategy_id=self.strategy_id,
            confidence=round(min(1.0, 0.45 + (fundamental_score - 65) / 100), 3),
            reason=f"Scan list #{top.index(ticker.upper()) + 1} fund={fundamental_score:.0f}",
            entry_price=round(close, 4),
            stop_loss_price=stop,
            take_profit_price=target,
            avg_win_pct=self.expected_win_pct,
            avg_loss_pct=self.expected_loss_pct,
            meta={"scan_rank": top.index(ticker.upper()) + 1, "scan_list": top},
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
        return None
