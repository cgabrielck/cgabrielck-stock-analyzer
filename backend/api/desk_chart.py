"""Desk Chart payload (M4 presentation) — daily snapshot + closed indicators.

Aligns bar fetch with ``equity_ohlcv`` (Polygon when keyed, Yahoo labeled
fallback). Empty history is 404; never invents closes; never stamps
verified/live; never imports OrderManager / desk order submit / lab debate.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import HTTPException

from backend.utils.desk_indicators import INDICATOR_PARAMS, compute_closed_set
from backend.utils.equity_ohlcv import PERIOD_LOOKBACK_DAYS, fetch_daily_ohlcv

ALLOWED_PERIODS = ("1mo", "3mo", "6mo", "1y", "2y")
DEFAULT_PERIOD = "6mo"
_MAX_SYMBOL_LEN = 16


def _bar_date(idx: Any) -> str:
    ts = pd.Timestamp(idx)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York")
    return ts.strftime("%Y-%m-%d")


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


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _normalize_period(period: Optional[str]) -> str:
    token = (period or DEFAULT_PERIOD).strip().lower()
    if token not in ALLOWED_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported period. Allowed: {', '.join(ALLOWED_PERIODS)}",
        )
    if token not in PERIOD_LOOKBACK_DAYS:
        raise HTTPException(status_code=400, detail="Unsupported period")
    return token


def _frame_to_bars(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    bars: List[Dict[str, Any]] = []
    for idx, row in frame.iterrows():
        open_ = _finite(row.get("Open"))
        high = _finite(row.get("High"))
        low = _finite(row.get("Low"))
        close = _finite(row.get("Close"))
        if open_ is None or high is None or low is None or close is None:
            continue
        if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
            continue
        volume = _finite(row.get("Volume"))
        bars.append(
            {
                "t": _bar_date(idx),
                "o": round(open_, 4),
                "h": round(high, 4),
                "l": round(low, 4),
                "c": round(close, 4),
                "v": int(volume) if volume is not None else None,
            }
        )
    return bars


def _empty_404(symbol: str, *, vendor: str, fallback: bool, reason: Optional[str]) -> None:
    raise HTTPException(
        status_code=404,
        detail={
            "message": "No chart data",
            "symbol": symbol,
            "vendor": vendor or "none",
            "asof": None,
            "fallback": bool(fallback),
            "fallback_reason": reason,
        },
    )


def chart_payload(symbol: str, period: str = DEFAULT_PERIOD) -> Dict[str, Any]:
    """Build GET /api/chart/{symbol} body. Raises HTTPException on empty/bad input."""
    ticker = _normalize_symbol(symbol)
    if not ticker or len(ticker) > _MAX_SYMBOL_LEN:
        _empty_404(ticker or "", vendor="none", fallback=False, reason="invalid_symbol")

    window = _normalize_period(period)
    fetched = fetch_daily_ohlcv(ticker, period=window, min_bars=1)
    vendor = str(fetched.get("provider") or "none")
    fallback = bool(fetched.get("fallback"))
    reason = fetched.get("reason")
    if not fetched.get("ok") or fetched.get("data") is None:
        _empty_404(ticker, vendor=vendor, fallback=fallback, reason=reason)

    frame = fetched["data"]
    if frame is None or getattr(frame, "empty", True):
        _empty_404(ticker, vendor=vendor, fallback=fallback, reason=reason or "empty")

    bars = _frame_to_bars(frame)
    if not bars:
        _empty_404(ticker, vendor=vendor, fallback=fallback, reason=reason or "empty")

    asof = bars[-1]["t"]
    payload: Dict[str, Any] = {
        "symbol": ticker,
        "interval": "1d",
        "period": window,
        "vendor": vendor,
        "asof": asof,
        "fallback": fallback,
        "fallback_reason": reason,
        "attempted": list(fetched.get("attempted") or []),
        "bars": bars,
        "indicators": compute_closed_set(frame),
        "indicator_params": INDICATOR_PARAMS,
        "source_kind": "daily_snapshot",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # Honesty: a loaded chart is not live/verified evidence.
    if payload.get("verified") or payload.get("live"):
        raise RuntimeError("chart payload must not stamp verified/live")
    return payload
