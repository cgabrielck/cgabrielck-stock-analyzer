"""
Polygon / Massive equity bars and ticker reference helpers.

Used as the preferred paid OHLCV path for the trading worker and evaluation
seals when POLYGON_API_KEY (or MASSIVE_API_KEY) is configured. Falls back is
handled by callers (typically yfinance via chart_utils).
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd
import requests
from config import get_secret

logger = logging.getLogger(__name__)

POLYGON_API_KEY = str(get_secret("POLYGON_API_KEY") or get_secret("MASSIVE_API_KEY") or "").strip()
POLYGON_BASE_URL = str(get_secret("POLYGON_BASE_URL") or "https://api.polygon.io").strip().rstrip("/")
DEFAULT_TIMEOUT = 15


def is_configured() -> bool:
    key = (POLYGON_API_KEY or "").strip()
    if not key:
        return False
    low = key.lower()
    if any(token in low for token in ("replace-with", "your-polygon", "changeme")):
        return False
    return True


def _get(path: str, params: Optional[Dict[str, Any]] = None, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    if not is_configured():
        return {"error": "not_configured", "reason": "api_key_missing"}
    query = dict(params or {})
    query["apiKey"] = POLYGON_API_KEY
    url = f"{POLYGON_BASE_URL}{path}"
    try:
        response = requests.get(url, params=query, timeout=timeout)
    except requests.RequestException as exc:
        return {"error": "request_failed", "reason": str(exc)}
    if response.status_code == 429:
        return {"error": "rate_limited", "reason": "HTTP 429"}
    if not response.ok:
        return {"error": "http_error", "reason": f"HTTP {response.status_code}"}
    try:
        payload = response.json()
    except ValueError:
        return {"error": "invalid_json", "reason": "response_not_json"}
    if not isinstance(payload, dict):
        return {"error": "invalid_payload", "reason": "expected_object"}
    return payload


def fetch_ticker_list_date(ticker: str) -> Optional[date]:
    """Return Polygon list_date for a ticker, if available."""
    symbol = ticker.strip().upper().replace(".", ".")
    # Polygon uses BRK.B style; our universe uses BRK-B.
    polygon_symbol = symbol.replace("-", ".")
    payload = _get(f"/v3/reference/tickers/{polygon_symbol}")
    if payload.get("error"):
        logger.debug("Polygon list_date miss for %s: %s", ticker, payload.get("reason"))
        return None
    results = payload.get("results") or {}
    raw = results.get("list_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def fetch_daily_bars(
    ticker: str,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    adjusted: bool = True,
    limit: int = 50000,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Fetch aggregate daily bars.

    Returns:
      {"ok": True, "data": DataFrame[open,high,low,close,volume], "provider": "polygon"}
      or {"ok": False, "error": ..., "reason": ...}
    """
    if not is_configured():
        return {"ok": False, "error": "not_configured", "reason": "api_key_missing"}

    symbol = ticker.strip().upper().replace("-", ".")
    end_day = end or datetime.now(timezone.utc).date().isoformat()
    start_day = start or "2018-01-01"
    path = f"/v2/aggs/ticker/{symbol}/range/1/day/{start_day}/{end_day}"
    params = {
        "adjusted": "true" if adjusted else "false",
        "sort": "asc",
        "limit": int(limit),
    }

    timeout = DEFAULT_TIMEOUT
    if deadline is not None:
        timeout = max(1.0, min(DEFAULT_TIMEOUT, deadline - time.monotonic()))

    payload = _get(path, params=params, timeout=timeout)
    if payload.get("error"):
        return {"ok": False, "error": payload["error"], "reason": payload.get("reason")}

    rows = payload.get("results") or []
    if not rows:
        return {"ok": False, "error": "empty", "reason": "no_bars"}

    frame = pd.DataFrame(rows)
    # Polygon aggregate fields: t(ms), o,h,l,c,v,vw,n
    rename = {"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume", "t": "timestamp"}
    frame = frame.rename(columns=rename)
    frame["Date"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True).dt.tz_convert(None)
    frame = frame.set_index("Date").sort_index()
    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in frame.columns]
    data = frame[cols].copy()
    return {
        "ok": True,
        "data": data,
        "provider": "polygon",
        "symbol": symbol,
        "adjusted": adjusted,
        "bar_count": len(data),
    }


def fetch_chart_data_polygon(ticker: str, period_hint: str = "1y") -> Dict[str, Any]:
    """
    chart_utils-compatible wrapper.

    period_hint is informational; Polygon uses explicit start/end. We map common
    hints to lookbacks so the worker can swap providers without API changes.
    """
    lookbacks = {
        "1mo": 31,
        "3mo": 93,
        "6mo": 186,
        "1y": 400,
        "2y": 800,
        "5y": 2000,
        "max": 5000,
    }
    days = lookbacks.get(period_hint, 400)
    end = datetime.now(timezone.utc).date()
    start = date.fromordinal(end.toordinal() - days)
    result = fetch_daily_bars(ticker, start=start.isoformat(), end=end.isoformat())
    if not result.get("ok"):
        return {"error": result.get("error"), "reason": result.get("reason"), "data": None}
    return {"data": result["data"], "provider": "polygon", "error": None}
