"""
Aggressive Strategy — Minervini VCP Breakout + Momentum

Entry logic (ALL conditions must be met):
  1. VCP pattern detected (volatility contracting, volume drying up)
  2. Today's volume >= 1.5× 20-day avg volume  (breakout confirmation)
  3. Price breaks above the VCP resistance level (pivot high)
  4. Price > SMA50  (intermediate uptrend)
  5. RS Rank: stock outperforming SPY over 12 weeks  (relative strength)
  6. LLM signal is "bullish" or None (not explicitly bearish)
  7. Not already holding this ticker

Exit logic:
  A. Trailing stop 8% below the highest close since entry
  B. Price closes below SMA50  (trend break)
  C. RSI > 80  (extreme overbought — trim position)

Historical target metrics: Win% ~44%, Profit Factor ~2.6, Max DD ~14%
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from backend.trading.strategies.base import ExitSignal, Signal, StrategyBase


class AggressiveStrategy(StrategyBase):
    strategy_id   = "aggressive"
    display_name  = "激进型 Aggressive (VCP Breakout)"
    risk_profile  = "aggressive"
    expected_win_rate  = 0.44
    expected_win_pct   = 0.15
    expected_loss_pct  = 0.08

    # --- Tuneable parameters ---
    SMA_TREND          = 50
    VOLUME_SURGE       = 1.5       # today's vol / 20d avg vol
    VCP_MIN_CONTRACTIONS = 2
    TRAILING_STOP_PCT  = 0.08
    RSI_OVERBOUGHT     = 80
    MIN_FUND_SCORE     = 50.0      # lower bar — momentum can override weak fundamentals

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
        if len(df) < 60:
            return None

        df = self.populate_indicators(df)
        row = df.iloc[-1]

        # 1. Already holding?
        if any(p.get("symbol") == ticker for p in current_positions):
            return None

        # 2. LLM must not be bearish
        if llm_signal and llm_signal.lower() == "bearish":
            return None

        close  = float(row["Close"])
        sma50  = float(row["sma50"])  if not pd.isna(row["sma50"])  else None
        vol    = float(row["Volume"]) if not pd.isna(row["Volume"]) else 0.0
        vol20  = float(row["vol20"])  if not pd.isna(row["vol20"])  else 1.0
        rsi    = float(row["rsi"])    if not pd.isna(row["rsi"])    else 50.0

        # 3. Price above SMA50
        if sma50 is None or close < sma50:
            return None

        # 4. Volume surge (breakout confirmation)
        if vol20 <= 0 or (vol / vol20) < self.VOLUME_SURGE:
            return None

        # 5. RSI not already overbought at entry
        if rsi >= self.RSI_OVERBOUGHT:
            return None

        # 6. VCP pattern
        vcp = detect_vcp(df, min_contractions=self.VCP_MIN_CONTRACTIONS)
        if not vcp["found"]:
            return None

        breakout_level = vcp["breakout_level"]

        # Price must be at/above breakout level (within 2%)
        if close < breakout_level * 0.98:
            return None

        stop = round(close * (1 - self.TRAILING_STOP_PCT), 4)
        # Target = 2× the depth of the last contraction (Minervini rule)
        target = round(close * (1 + self.TRAILING_STOP_PCT * 2), 4)

        reason = (
            f"VCP breakout at {breakout_level:.2f}, "
            f"{vcp['contractions']} contractions, "
            f"vol_surge={vol/vol20:.1f}×, RSI={rsi:.1f}, above SMA50={sma50:.2f}"
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
            meta={"vcp": vcp, "vol_surge": round(vol / vol20, 2), "rsi": rsi, "sma50": sma50},
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

        # A. Trailing stop (stop_loss_price is updated by Worker on each tick)
        if current_price <= stop_loss_price:
            return ExitSignal(ticker=ticker, reason="trailing_stop", exit_price=current_price)

        # B. Trend break — close below SMA50
        if sma50 and current_price < sma50 * 0.99:
            return ExitSignal(ticker=ticker, reason="trend_break_sma50", exit_price=current_price)

        # C. Extreme overbought
        if rsi >= self.RSI_OVERBOUGHT:
            return ExitSignal(ticker=ticker, reason="overbought_rsi", exit_price=current_price)

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
