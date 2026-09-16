"""Daily equity OHLCV with Polygon preferred and Yahoo as labeled fallback.

Worker, Scan technicals, and backtests share this selector so provenance can
record the actual vendor. Placeholder POLYGON_API_KEY values do not count as
configured. Bars are filtered to US equity sessions (no 24h/BTC dates).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    from backend.agents import polygon_equity
except ImportError:  # Scan path: backend/ on sys.path
    from agents import polygon_equity  # type: ignore

try:
    from backend.utils.us_equity_calendar import filter_us_equity_daily_bars
except ImportError:
    from utils.us_equity_calendar import filter_us_equity_daily_bars  # type: ignore

PERIOD_LOOKBACK_DAYS = {
    "5d": 12,
    "1mo": 31,
    "3mo": 93,
    "6mo": 186,
    "1y": 400,
    "2y": 800,
    "5y": 2000,
    "max": 5000,
}


def _period_window(period: Optional[str]) -> tuple[str, str]:
    end = datetime.now(timezone.utc).date()
    days = PERIOD_LOOKBACK_DAYS.get(period or "1y", 400)
    start = date.fromordinal(end.toordinal() - days)
    return start.isoformat(), end.isoformat()


def fetch_daily_ohlcv(
    ticker: str,
    *,
    period: Optional[str] = "1y",
    start: Optional[str] = None,
    end: Optional[str] = None,
    min_bars: int = 1,
) -> Dict[str, Any]:
    """Fetch daily OHLCV for a US equity.

    Returns:
      ok, data, provider, fallback, attempted, error, reason
    """
    attempted: List[str] = []
    start_day, end_day = (start, end) if start and end else _period_window(period)
    keyed = bool(polygon_equity.is_configured())

    if keyed:
        attempted.append("polygon")
        poly_error = "empty"
        try:
            poly = polygon_equity.fetch_daily_bars(ticker, start=start_day, end=end_day)
            if poly.get("ok") and poly.get("data") is not None:
                frame = filter_us_equity_daily_bars(poly["data"])
                if frame is not None and not frame.empty and len(frame) >= min_bars:
                    return {
                        "ok": True,
                        "data": frame,
                        "provider": "polygon",
                        "fallback": False,
                        "attempted": attempted,
                        "error": None,
                        "reason": None,
                    }
            poly_error = str(poly.get("reason") or poly.get("error") or "empty")
        except Exception as exc:
            poly_error = str(exc)
    else:
        poly_error = "not_configured"

    attempted.append("yahoo")
    yahoo = _fetch_yahoo_daily(
        ticker,
        period=None if (start and end) else period,
        start=start or start_day,
        end=end or end_day,
    )
    if yahoo is not None:
        frame = filter_us_equity_daily_bars(yahoo)
        if frame is not None and not frame.empty and len(frame) >= min_bars:
            return {
                "ok": True,
                "data": frame,
                "provider": "yahoo",
                "fallback": keyed,
                "attempted": attempted,
                "error": None,
                "reason": "yahoo_fallback" if keyed else None,
            }

    return {
        "ok": False,
        "data": None,
        "provider": "none",
        "fallback": keyed,
        "attempted": attempted,
        "error": "empty",
        "reason": poly_error if keyed else "yahoo_empty",
    }


def _fetch_yahoo_daily(
    ticker: str,
    *,
    period: Optional[str],
    start: Optional[str],
    end: Optional[str],
) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        if start and end:
            hist = stock.history(start=start, end=end, interval="1d", auto_adjust=False)
        else:
            hist = stock.history(period=period or "1y", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in hist.columns]
        if not cols:
            return None
        return hist[cols].copy()
    except Exception:
        return None
