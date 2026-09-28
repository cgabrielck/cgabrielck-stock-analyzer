"""Adaptive router — regime chooses Trend / Breakout / Reversion; exits follow entry kind."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from backend.trading.strategies.aggressive import AggressiveStrategy
from backend.trading.strategies.base import ExitSignal, Signal, StrategyBase
from backend.trading.strategies.stable import StableStrategy
from backend.trading.strategies.stage2 import classify_spy_regime, stage2_ok, add_trend_template_columns
from backend.trading.strategies.trend import TrendStrategy


class AdaptiveStrategy(StrategyBase):
    """
    Market regime router (not OR of two signals):
      - bull + Stage-2 → trend, then breakout if trend silent
      - range + Stage-2 → reversion (Connors-style)
      - bear / high-vol → no new buys (regime_no_new_buys)
    Exits always use the child that opened the position.
    """

    strategy_id = "adaptive"
    display_name = "自適應 Adaptive (regime router)"
    risk_profile = "hybrid"
    expected_win_rate = 0.46
    expected_win_pct = 0.14
    expected_loss_pct = 0.07

    def __init__(self, **overrides):
        self.spy_close: Optional[pd.Series] = None
        self._asof = None
        self._leg_by_ticker: Dict[str, str] = {}
        # Allow nested overrides only for shared float knobs on children
        self._trend = TrendStrategy(**{k: v for k, v in overrides.items() if hasattr(TrendStrategy, k.upper())})
        self._breakout = AggressiveStrategy(
            **{k: v for k, v in overrides.items() if hasattr(AggressiveStrategy, k.upper())}
        )
        self._reversion = StableStrategy(
            **{k: v for k, v in overrides.items() if hasattr(StableStrategy, k.upper())}
        )

    def set_spy_close(self, spy_close: Optional[pd.Series]) -> None:
        self.spy_close = spy_close
        self._trend.set_spy_close(spy_close)
        if hasattr(self._breakout, "set_spy_close"):
            self._breakout.set_spy_close(spy_close)

    def set_asof(self, day) -> None:
        self._asof = day
        # Truncate spy for regime classification at as-of
        if self.spy_close is not None and day is not None:
            try:
                ts = pd.Timestamp(day)
                sub = self.spy_close.loc[:ts]
                self._trend.set_spy_close(sub)
                if hasattr(self._breakout, "set_spy_close"):
                    self._breakout.set_spy_close(sub)
            except Exception:
                pass

    def _regime(self) -> str:
        spy = self.spy_close
        if spy is not None and self._asof is not None:
            try:
                spy = spy.loc[: pd.Timestamp(self._asof)]
            except Exception:
                pass
        # Live path: prefer live detector when no spy series injected
        if spy is None or len(spy) < 210:
            try:
                from backend.agents.market_regime import detect_global_market_regime

                live = detect_global_market_regime() or {}
                r = str(live.get("regime") or "").lower()
                if "bear" in r or "high_vol" in r or "volatile" in r:
                    return "bear"
                if "bull" in r:
                    return "bull"
                return "range"
            except Exception:
                return "range"
        return classify_spy_regime(spy)

    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def diagnose_entry(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[str]:
        regime = self._regime()
        if regime == "bear":
            return "regime_no_new_buys"
        if df is None or len(df) < 60:
            return "history_short"
        work = add_trend_template_columns(df)
        ok, code = stage2_ok(work.iloc[-1]) if len(work) >= 210 else (False, "not_stage2")
        if regime == "bull":
            skip = self._trend.diagnose_entry(ticker, df, fundamental_score, llm_signal, current_positions)
            if skip is None:
                return None
            skip_b = self._breakout.diagnose_entry(ticker, df, fundamental_score, llm_signal, current_positions)
            if skip_b is None:
                return None
            return skip if not ok else (skip_b or skip)
        # range → reversion only if still Stage-2
        if not ok:
            return code or "not_stage2"
        return self._reversion.diagnose_entry(ticker, df, fundamental_score, llm_signal, current_positions)

    def generate_signal(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[Signal]:
        regime = self._regime()
        if regime == "bear":
            return None

        sig: Optional[Signal] = None
        kind = None
        if regime == "bull":
            sig = self._trend.generate_signal(ticker, df, fundamental_score, llm_signal, current_positions)
            kind = "trend"
            if sig is None:
                sig = self._breakout.generate_signal(
                    ticker, df, fundamental_score, llm_signal, current_positions
                )
                kind = "breakout"
        else:
            work = add_trend_template_columns(df) if df is not None and len(df) >= 210 else None
            if work is None or not stage2_ok(work.iloc[-1])[0]:
                return None
            sig = self._reversion.generate_signal(
                ticker, df, fundamental_score, llm_signal, current_positions
            )
            kind = "reversion"

        if sig is None:
            return None
        meta = dict(sig.meta or {})
        meta["entry_kind"] = kind
        meta["regime"] = regime
        self._leg_by_ticker[ticker.upper()] = kind or sig.strategy_id
        return Signal(
            ticker=sig.ticker,
            side=sig.side,
            strategy_id=self.strategy_id,
            confidence=sig.confidence,
            reason=f"[{regime}/{kind}] {sig.reason}",
            entry_price=sig.entry_price,
            stop_loss_price=sig.stop_loss_price,
            take_profit_price=sig.take_profit_price,
            avg_win_pct=self.expected_win_pct,
            avg_loss_pct=self.expected_loss_pct,
            meta=meta,
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
        kind = self._leg_by_ticker.get(ticker.upper())
        child = {
            "trend": self._trend,
            "breakout": self._breakout,
            "aggressive": self._breakout,
            "reversion": self._reversion,
            "stable": self._reversion,
        }.get(kind or "", self._trend)
        es = child.check_exit(
            ticker, entry_price, current_price, df, stop_loss_price, take_profit_price
        )
        if es:
            self._leg_by_ticker.pop(ticker.upper(), None)
        return es
