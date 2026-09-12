"""Minervini Stage-2 / relative-strength helpers shared by trend, breakout, adaptive."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd


def add_trend_template_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add SMA50/150/200, SMA21, ATR, 52w high, SMA200 slope for Stage-2 checks."""
    out = df.copy()
    close = out["Close"]
    out["sma21"] = close.rolling(21).mean()
    out["sma50"] = close.rolling(50).mean()
    out["sma150"] = close.rolling(150).mean()
    out["sma200"] = close.rolling(200).mean()
    out["sma200_slope"] = out["sma200"] - out["sma200"].shift(21)
    out["high_52w"] = out["High"].rolling(252, min_periods=60).max()
    if "atr" not in out.columns:
        high, low, prev = out["High"], out["Low"], close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev).abs(), (low - prev).abs()],
            axis=1,
        ).max(axis=1)
        out["atr"] = tr.ewm(span=14, adjust=False).mean()
    out["atr_ratio_10"] = out["atr"] / out["atr"].rolling(10).mean()
    return out


def stage2_ok(row: pd.Series) -> Tuple[bool, Optional[str]]:
    """Minervini Trend Template gate. Returns (ok, skip_code)."""
    try:
        close = float(row["Close"])
        sma50 = float(row["sma50"])
        sma150 = float(row["sma150"])
        sma200 = float(row["sma200"])
        slope = float(row["sma200_slope"])
    except (KeyError, TypeError, ValueError):
        return False, "history_short"
    if any(pd.isna(x) for x in (close, sma50, sma150, sma200, slope)):
        return False, "history_short"
    if not (close > sma50 > sma150 > sma200):
        return False, "not_stage2"
    if slope <= 0:
        return False, "not_stage2"
    return True, None


def near_52w_high(row: pd.Series, max_drawdown_from_high: float = 0.25) -> Tuple[bool, Optional[str]]:
    """Require price within *max_drawdown_from_high* of 52-week high (avoid broken names)."""
    try:
        close = float(row["Close"])
        hi = float(row["high_52w"])
    except (KeyError, TypeError, ValueError):
        return False, "history_short"
    if pd.isna(hi) or hi <= 0:
        return False, "history_short"
    if close < hi * (1.0 - max_drawdown_from_high):
        return False, "extended"  # used as "too far from highs / broken"
    return True, None


def pullback_entry_ok(row: pd.Series, band_pct: float = 0.03, *, prior_closes: Optional[pd.Series] = None) -> Tuple[bool, Optional[str]]:
    """Elder-style timing: must have dipped to/under SMA21 recently, then reclaim."""
    try:
        close = float(row["Close"])
        sma21 = float(row["sma21"])
        sma50 = float(row["sma50"])
    except (KeyError, TypeError, ValueError):
        return False, "not_pullback"
    if any(pd.isna(x) for x in (close, sma21, sma50)):
        return False, "not_pullback"
    # Prefer reclaim of SMA21 after a touch; SMA50 allowed as secondary
    near_21 = close >= sma21 * 0.997 and abs(close - sma21) / sma21 <= band_pct
    near_50 = close >= sma50 * 0.997 and abs(close - sma50) / sma50 <= band_pct
    dipped = False
    if prior_closes is not None and len(prior_closes) >= 5:
        recent = prior_closes.iloc[-6:-1]
        dipped = bool((recent < sma21 * 1.002).any())
    if (near_21 or near_50) and dipped:
        return True, None
    return False, "not_pullback"


def relative_strength_vs_spy(
    stock_close: pd.Series,
    spy_close: Optional[pd.Series],
    lookback: int = 63,
) -> Tuple[Optional[float], Optional[str]]:
    """12-week (≈63 trading days) relative return vs SPY. Positive = outperforming."""
    if spy_close is None or len(stock_close) < lookback + 1 or len(spy_close) < lookback + 1:
        return None, "rs_weak"
    s = stock_close.iloc[-(lookback + 1) :]
    b = spy_close.reindex(s.index).dropna()
    s = s.reindex(b.index).dropna()
    if len(s) < lookback or len(b) < lookback:
        return None, "rs_weak"
    stock_ret = float(s.iloc[-1] / s.iloc[0] - 1.0)
    spy_ret = float(b.iloc[-1] / b.iloc[0] - 1.0)
    return stock_ret - spy_ret, None


def classify_spy_regime(spy_close: Optional[pd.Series]) -> str:
    """Lightweight Weinstein-style SPY regime for adaptive routing: bull | range | bear."""
    if spy_close is None or len(spy_close) < 210:
        return "range"
    close = spy_close.astype(float)
    sma200 = close.rolling(200).mean()
    c = float(close.iloc[-1])
    m = float(sma200.iloc[-1])
    if pd.isna(m):
        return "range"
    ret20 = float(close.iloc[-1] / close.iloc[-21] - 1.0) if len(close) > 21 else 0.0
    if c < m * 0.97 or ret20 < -0.06:
        return "bear"
    if c > m and ret20 > -0.03:
        return "bull"
    return "range"


def atr_stop(close: float, atr: float, multiple: float = 1.8) -> float:
    if atr <= 0 or pd.isna(atr):
        return round(close * 0.92, 4)
    return round(close - multiple * atr, 4)


def size_shares_by_risk(
    *,
    equity: float,
    cash: float,
    fill_price: float,
    stop_price: float,
    risk_pct: float = 0.01,
    max_position_pct: float = 0.25,
) -> int:
    """Van Tharp: shares = (equity * risk_pct) / |entry - stop|, capped by cash and concentration."""
    if fill_price <= 0 or equity <= 0:
        return 0
    risk_per_share = fill_price - stop_price
    if risk_per_share <= 0:
        risk_per_share = fill_price * 0.05
    budget = equity * risk_pct
    qty = int(budget / risk_per_share)
    max_by_conc = int((equity * max_position_pct) / fill_price)
    max_by_cash = int(cash / fill_price)
    qty = max(0, min(qty, max_by_conc, max_by_cash))
    return qty
