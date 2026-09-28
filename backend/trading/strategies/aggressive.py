"""
Breakout Strategy (id: aggressive) — Minervini VCP + RS filter + MACD hist gate.

Entry logic (ALL conditions must be met):
  1. VCP pattern detected (volatility contracting)
  2. Today's volume >= 1.5× 20-day avg volume
  3. Price breaks above the VCP resistance level
  4. Price > SMA50
  5. 12-week RS vs SPY > 0 (6m/12m recorded as filter/sort meta only)
  6. MACD histogram ≥ 0 (same helper as diagnose / worker / backtest)
  7. LLM not explicitly bearish
  8. Not already holding this ticker

Exit:
  A. ATR / pivot stop (worker may trail)
  B. Close below SMA50
  C. No RSI≥80 scalp exit — let winners run to ≥2R then trail

Legacy id remains ``aggressive``; ``breakout`` is an alias in the registry.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from backend.trading.strategies.base import ExitSignal, Signal, StrategyBase
from backend.trading.strategies.stage2 import atr_stop, macd_hist_ok, rs_entry_ok


class AggressiveStrategy(StrategyBase):
    strategy_id   = "aggressive"
    display_name  = "突破型 Breakout (VCP + RS)"
    risk_profile  = "aggressive"
    expected_win_rate  = 0.40
    expected_win_pct   = 0.16
    expected_loss_pct  = 0.07

    # --- Tuneable parameters ---
    SMA_TREND          = 50
    VOLUME_SURGE       = 1.5
    VCP_MIN_CONTRACTIONS = 2
    TRAILING_STOP_PCT  = 0.08  # fallback if ATR missing
    ATR_STOP_MULT      = 1.8
    RSI_OVERBOUGHT     = 80    # kept for diagnose only (not a required exit)
    MIN_FUND_SCORE     = 50.0
    REWARD_RISK_RATIO  = 3.0
    RS_LOOKBACK        = 63

    def __init__(self, **overrides):
        self.spy_close: Optional[pd.Series] = None
        for key, value in overrides.items():
            attr = key.upper()
            if hasattr(self, attr):
                setattr(self, attr, float(value) if isinstance(value, (int, float)) else value)

    def set_spy_close(self, spy_close: Optional[pd.Series]) -> None:
        self.spy_close = spy_close

    def diagnose_entry(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ) -> Optional[str]:
        skip, _packed = self._entry_skip(ticker, df, fundamental_score, llm_signal, current_positions)
        return skip

    def _entry_skip(
        self,
        ticker: str,
        df: pd.DataFrame,
        fundamental_score: float,
        llm_signal: Optional[str],
        current_positions: List[Dict[str, Any]],
    ):
        if df is None or len(df) < 60:
            return "history_short", None
        work = self.populate_indicators(df)
        row = work.iloc[-1]
        if any(p.get("symbol") == ticker for p in current_positions):
            return "already_held", None
        if llm_signal and llm_signal.lower() == "bearish":
            return "llm_bearish", None
        if fundamental_score < self.MIN_FUND_SCORE:
            return "fund_below_min", None
        close = float(row["Close"])
        sma50 = float(row["sma50"]) if not pd.isna(row["sma50"]) else None
        vol = float(row["Volume"]) if not pd.isna(row["Volume"]) else 0.0
        vol20 = float(row["vol20"]) if not pd.isna(row["vol20"]) else 1.0
        rsi = float(row["rsi"]) if not pd.isna(row["rsi"]) else 50.0
        if sma50 is None or close < sma50:
            return "below_sma50", None
        if vol20 <= 0 or (vol / vol20) < self.VOLUME_SURGE:
            return "volume_weak", None
        # Do not require RSI not overbought for entry — Stage-2 breakouts often print high RSI.
        rs_ok, rs_code, rs_bundle = rs_entry_ok(work["Close"], self.spy_close, hard_lookback=int(self.RS_LOOKBACK))
        if not rs_ok:
            return rs_code or "rs_weak", None
        macd_ok, macd_code = macd_hist_ok(row.get("macd_hist"))
        if not macd_ok:
            return macd_code or "macd_weak", None
        vcp = detect_vcp(work, min_contractions=int(self.VCP_MIN_CONTRACTIONS))
        if not vcp["found"]:
            return "no_vcp", None
        breakout_level = vcp["breakout_level"]
        if close < breakout_level * 0.98:
            return "no_vcp", None
        return None, (row, close, sma50, vol, vol20, rsi, vcp, breakout_level, rs_bundle)

    def populate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["sma50"]     = df["Close"].rolling(50).mean()
        df["vol20"]     = df["Volume"].rolling(20).mean()
        df["rsi"]       = self._rsi(df["Close"])
        df["atr"]       = self._atr(df)
        macd, sig, hist = self._macd(df["Close"])
        df["macd"]      = macd
        df["macd_sig"]  = sig
        df["macd_hist"] = hist
        return df

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
        row, close, sma50, vol, vol20, rsi, vcp, breakout_level, rs_bundle = packed
        rs = float(rs_bundle.get("rs_63") or 0.0)
        macd_hist = float(row["macd_hist"]) if not pd.isna(row.get("macd_hist")) else 0.0

        atr = float(row["atr"]) if "atr" in row and not pd.isna(row["atr"]) else close * 0.02
        pivot_stop = round(breakout_level * 0.98, 4)
        atr_s = atr_stop(close, atr, self.ATR_STOP_MULT)
        pct_stop = round(close * (1 - self.TRAILING_STOP_PCT), 4)
        # Nearest valid stop below price among ATR / pivot / pct
        candidates = [s for s in (atr_s, pivot_stop, pct_stop) if s < close]
        stop = round(max(candidates), 4) if candidates else pct_stop
        initial_risk = close - stop
        target = round(close + self.REWARD_RISK_RATIO * initial_risk, 4)

        reason = (
            f"VCP breakout at {breakout_level:.2f}, "
            f"{vcp['contractions']} contractions, "
            f"vol_surge={vol/vol20:.1f}×, RS_12w={rs*100:.1f}% (filter), "
            f"MACD_hist={macd_hist:.4f}, SMA50={sma50:.2f}"
        )

        return Signal(
            ticker=ticker,
            side="buy",
            strategy_id=self.strategy_id,
            confidence=round(min(1.0, 0.4 + vcp["contractions"] * 0.1 + (vol / vol20 - 1) * 0.05), 3),
            reason=reason,
            entry_price=round(close, 4),
            stop_loss_price=stop,
            take_profit_price=target,
            avg_win_pct=self.expected_win_pct,
            avg_loss_pct=self.expected_loss_pct,
            meta={
                "entry_kind": "breakout",
                "vcp": vcp,
                "vol_surge": round(vol / vol20, 2),
                "rsi": rsi,
                "sma50": sma50,
                "rs_role": "filter_only",
                "rs_12w": None if rs_bundle.get("rs_63") is None else round(float(rs_bundle["rs_63"]), 4),
                "rs_126": None if rs_bundle.get("rs_126") is None else round(float(rs_bundle["rs_126"]), 4),
                "rs_252": None if rs_bundle.get("rs_252") is None else round(float(rs_bundle["rs_252"]), 4),
                "macd_hist": round(macd_hist, 6),
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

        df = self.populate_indicators(df)
        row = df.iloc[-1]
        sma50 = float(row["sma50"]) if not pd.isna(row["sma50"]) else None
        rsi   = float(row["rsi"])   if not pd.isna(row["rsi"])   else 50.0

        # A. Trailing / hard stop (stop_loss_price may be updated by Worker)
        if current_price <= stop_loss_price:
            return ExitSignal(ticker=ticker, reason="trailing_stop", exit_price=current_price)

        # B. Trend break — close below SMA50
        if sma50 and current_price < sma50 * 0.99:
            return ExitSignal(ticker=ticker, reason="trend_break_sma50", exit_price=current_price)

        # Intentionally no RSI≥80 scalp exit — breakouts often stay "overbought".
        _ = rsi
        return None


# ---------------------------------------------------------------------------
# VCP Detection  (Minervini Volatility Contraction Pattern)
# ---------------------------------------------------------------------------

def detect_vcp(df: pd.DataFrame, min_contractions: int = 2) -> Dict:
    """
    Detects a Volatility Contraction Pattern (VCP) in the supplied DataFrame.

    Algorithm:
      1. Find swing highs and lows over a rolling 10-bar window.
      2. Measure the depth of each pullback (swing-high to swing-low).
      3. A "contraction" occurs when each pullback is shallower than the previous.
      4. Also verify volume is declining across contractions.
      5. The breakout level = most recent swing high.

    Returns dict with keys:
      found (bool), contractions (int), breakout_level (float),
      last_pullback_pct (float), avg_volume_trend (str)
    """
    from typing import Dict as DictT
    result: DictT = {"found": False, "contractions": 0, "breakout_level": 0.0,
                     "last_pullback_pct": 0.0, "avg_volume_trend": "flat"}

    if len(df) < 30:
        return result

    window = 10
    highs, lows = [], []

    for i in range(window, len(df) - window):
        h = float(df["High"].iloc[i])
        l = float(df["Low"].iloc[i])
        local_high = float(df["High"].iloc[i - window:i + window + 1].max())
        local_low  = float(df["Low"].iloc[i - window:i + window + 1].min())
        if h >= local_high:
            highs.append((i, h))
        if l <= local_low:
            lows.append((i, l))

    if len(highs) < 2 or len(lows) < 2:
        return result

    # Build pullback depths between alternating highs and lows
    pullbacks = []
    events = sorted(highs + lows, key=lambda x: x[0])
    prev_high = None
    for idx, price in events:
        if (idx, price) in highs:
            prev_high = (idx, price)
        elif prev_high is not None and (idx, price) in lows:
            depth = (prev_high[1] - price) / prev_high[1]
            # avg volume in the pullback window
            vol_slice = df["Volume"].iloc[prev_high[0]:idx + 1]
            avg_vol = float(vol_slice.mean()) if len(vol_slice) > 0 else 0.0
            pullbacks.append({"depth": depth, "high": prev_high[1], "low": price,
                               "high_idx": prev_high[0], "low_idx": idx, "avg_vol": avg_vol})

    if len(pullbacks) < min_contractions:
        return result

    # Check that pullbacks are contracting (each shallower than previous)
    recent = pullbacks[-min_contractions - 1:]   # look at last N+1 pullbacks
    contractions = 0
    for i in range(1, len(recent)):
        if recent[i]["depth"] < recent[i - 1]["depth"] * 0.9:   # 10% shallower
            contractions += 1

    if contractions < min_contractions:
        return result

    # Volume contraction check: avg vol declining across pullbacks
    if len(recent) >= 2:
        vol_trend = "declining" if recent[-1]["avg_vol"] < recent[0]["avg_vol"] else "flat"
    else:
        vol_trend = "flat"

    # Breakout level = most recent confirmed swing high
    breakout_level = float(highs[-1][1])
    last_pullback_pct = round(recent[-1]["depth"] * 100, 2)

    result.update({
        "found": True,
        "contractions": contractions,
        "breakout_level": round(breakout_level, 4),
        "last_pullback_pct": last_pullback_pct,
        "avg_volume_trend": vol_trend,
    })
    return result


BreakoutStrategy = AggressiveStrategy
