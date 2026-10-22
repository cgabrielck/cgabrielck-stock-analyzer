"""Closed v0 Desk indicator series (SMA/EMA/BB overlays; RSI/MACD panes).

Formulas match the Streamlit lab Plotly chart (rolling SMA, Wilder RSI,
MACD 12/26/9) so Desk overlays are derived from the same OHLCV — not a
second invented series. Warm-up bars are null; never fill fake values.

MUST NEVER: place orders, import OrderManager, or stamp verified/live.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import pandas as pd

INDICATOR_PARAMS: Dict[str, Any] = {
    "sma20": {"length": 20},
    "sma50": {"length": 50},
    "ema20": {"span": 20},
    "bb": {"length": 20, "std": 2},
    "rsi": {"length": 14},
    "macd": {"fast": 12, "slow": 26, "signal": 9},
}


def _finite(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _points(index: pd.Index, series: pd.Series, *, ndigits: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, raw in zip(index, series):
        value = _finite(raw)
        if value is None:
            continue
        out.append({"t": _bar_date(idx), "v": round(value, ndigits)})
    return out


def _bar_date(idx: Any) -> str:
    ts = pd.Timestamp(idx)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York")
    return ts.strftime("%Y-%m-%d")


def sma(close: pd.Series, length: int) -> pd.Series:
    return close.rolling(length, min_periods=length).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False, min_periods=span).mean()


def bollinger(close: pd.Series, length: int = 20, std: float = 2.0) -> Dict[str, pd.Series]:
    mid = sma(close, length)
    dev = close.rolling(length, min_periods=length).std()
    return {
        "middle": mid,
        "upper": mid + dev * std,
        "lower": mid - dev * std,
    }


def rsi_wilder(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder RSI (alpha=1/length), same shape as Streamlit `_build_tech_chart`."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    line = ema_fast - ema_slow
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = line - signal_line
    return {"macd": line, "signal": signal_line, "hist": hist}


def compute_closed_set(frame: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
    """Return sparse {t,v} series for the GC7 closed indicator set."""
    if frame is None or frame.empty or "Close" not in frame.columns:
        return {
            "sma20": [],
            "sma50": [],
            "ema20": [],
            "bb_upper": [],
            "bb_middle": [],
            "bb_lower": [],
            "rsi14": [],
            "macd": [],
            "macd_signal": [],
            "macd_hist": [],
        }
    close = pd.to_numeric(frame["Close"], errors="coerce")
    index = frame.index
    bb = bollinger(close, **INDICATOR_PARAMS["bb"])
    macd_s = macd(close, **INDICATOR_PARAMS["macd"])
    return {
        "sma20": _points(index, sma(close, 20), ndigits=4),
        "sma50": _points(index, sma(close, 50), ndigits=4),
        "ema20": _points(index, ema(close, 20), ndigits=4),
        "bb_upper": _points(index, bb["upper"], ndigits=4),
        "bb_middle": _points(index, bb["middle"], ndigits=4),
        "bb_lower": _points(index, bb["lower"], ndigits=4),
        "rsi14": _points(index, rsi_wilder(close, 14), ndigits=4),
        "macd": _points(index, macd_s["macd"], ndigits=6),
        "macd_signal": _points(index, macd_s["signal"], ndigits=6),
        "macd_hist": _points(index, macd_s["hist"], ndigits=6),
    }
