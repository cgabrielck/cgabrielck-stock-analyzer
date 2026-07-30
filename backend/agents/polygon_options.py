import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from config import get_secret


POLYGON_API_KEY = str(get_secret("POLYGON_API_KEY") or get_secret("MASSIVE_API_KEY") or "").strip()
POLYGON_BASE_URL = str(get_secret("POLYGON_BASE_URL") or "https://api.polygon.io").strip().rstrip("/")
MAX_QUOTE_AGE_MINUTES = 2


def is_configured() -> bool:
    return bool(POLYGON_API_KEY)


def fetch_polygon_options_chain(
    ticker: str, current_price: Optional[float] = None, deadline: Optional[float] = None,
) -> Dict[str, Any]:
    if not is_configured():
        return _error("not_configured", "api_key_missing")
    symbol = ticker.strip().upper()
    today = datetime.now(timezone.utc).date()
    deadline = deadline or time.monotonic() + 15
    contracts = _get("/v3/reference/options/contracts", {
        "underlying_ticker": symbol,
        "expiration_date.gte": today.isoformat(),
        "expiration_date.lte": (today + timedelta(days=120)).isoformat(),
        "expired": "false", "limit": 1000, "sort": "expiration_date", "order": "asc",
    }, deadline)
    if contracts.get("error"):
        return contracts
    reference_rows = list(contracts.get("results") or [])
    partial = False
    reference_next_url = contracts.get("next_url")
    for _ in range(4):
        if not reference_next_url:
            break
        if not _trusted_page_url(str(reference_next_url)):
            partial = True
            break
        page = _get_url(str(reference_next_url), {}, deadline)
        if page.get("error"):
            partial = True
            break
        reference_rows.extend(page.get("results") or [])
        reference_next_url = page.get("next_url")
    if reference_next_url:
        partial = True
    expirations = sorted({row.get("expiration_date") for row in reference_rows if row.get("expiration_date")})
    if not expirations:
        return _error("provider_incomplete", "empty_expirations")
    expiry = _select_expiry(expirations)
    snapshot_params: Dict[str, Any] = {
        "expiration_date": expiry, "limit": 250, "sort": "strike_price", "order": "asc",
    }
    if current_price and current_price > 0:
        snapshot_params.update({
            "strike_price.gte": round(float(current_price) * 0.7, 2),
            "strike_price.lte": round(float(current_price) * 1.3, 2),
        })
    snapshot = _get(f"/v3/snapshot/options/{symbol}", snapshot_params, deadline)
    if snapshot.get("error"):
        return snapshot
    rows = list(snapshot.get("results") or [])
    next_url = snapshot.get("next_url")
    for _ in range(4):
        if not next_url:
            break
        if not _trusted_page_url(str(next_url)):
            partial = True
            break
        page = _get_url(str(next_url), {}, deadline)
        if page.get("error"):
            partial = True
            break
        rows.extend(page.get("results") or [])
        next_url = page.get("next_url")
    if next_url:
        partial = True
    normalized = [_normalize(row) for row in rows if isinstance(row, dict)]
    normalized = [row for row in normalized if row]
    if not normalized:
        return _error("provider_incomplete", "empty_snapshot")
    spot = _number(current_price) or next(
        (_number((row.get("underlying_asset") or {}).get("price")) for row in rows if (row.get("underlying_asset") or {}).get("price") is not None),
        None,
    )
    calls = _nearest([row for row in normalized if row["option_type"] == "call"], spot)
    puts = _nearest([row for row in normalized if row["option_type"] == "put"], spot)
    timeframe_values = {row.get("timeframe", "UNKNOWN") for row in normalized}
    actionable = not partial and any(row["actionable"] for row in [*calls, *puts])
    return {
        "ticker": symbol, "expirations": expirations,
        "nearest_expiry": expirations[0], "selected_expiry": expiry,
        "num_calls": len([row for row in normalized if row["option_type"] == "call"]),
        "num_puts": len([row for row in normalized if row["option_type"] == "put"]),
        "atm_strike": min((row["strike"] for row in normalized), key=lambda value: abs(value - spot)) if spot else None,
        "put_call_ratio": len(puts) / len(calls) if calls else None,
        "put_call_volume_ratio": _ratio(normalized, "volume"),
        "put_call_oi_ratio": _ratio(normalized, "open_interest"),
        "calls": calls, "puts": puts,
        "source": "polygon_options", "timeframes": sorted(timeframe_values),
        "delayed": not actionable, "actionable": actionable,
        "partial": partial,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "as_of": max((row.get("quote_time") for row in normalized if row.get("quote_time")), default=None),
        "from_cache": False,
    }


def fetch_polygon_option_contract(underlying: str, contract_symbol: str) -> Dict[str, Any]:
    if not is_configured():
        return _error("not_configured", "api_key_missing")
    polygon_symbol = contract_symbol if contract_symbol.startswith("O:") else f"O:{contract_symbol}"
    snapshot = _get(f"/v3/snapshot/options/{underlying.strip().upper()}/{polygon_symbol}", {})
    if snapshot.get("error"):
        return snapshot
    row = snapshot.get("results") or snapshot
    contract = _normalize(row) if isinstance(row, dict) else None
    if not contract:
        return _error("provider_incomplete", "contract_not_found")
    if contract["contract_symbol"] != contract_symbol.removeprefix("O:"):
        return _error("provider_incomplete", "contract_mismatch")
    return contract


def _get(path: str, params: Dict[str, Any], deadline: Optional[float] = None) -> Dict[str, Any]:
    return _get_url(f"{POLYGON_BASE_URL}{path}", params, deadline)


def _get_url(url: str, params: Dict[str, Any], deadline: Optional[float] = None) -> Dict[str, Any]:
    remaining = (deadline - time.monotonic()) if deadline else 7.0
    if remaining <= 0:
        return _error("provider_error", "provider_timeout")
    try:
        response = requests.get(
            url, params={**params, "apiKey": POLYGON_API_KEY},
            timeout=(min(3.05, remaining), min(7.0, remaining)),
        )
    except requests.RequestException:
        return _error("provider_error", "request_failed")
    if response.status_code == 429:
        return _error("rate_limit", "http_429")
    if response.status_code in {401, 403}:
        return _error("authentication", f"http_{response.status_code}")
    if not response.ok:
        return _error("provider_error", f"http_{response.status_code}")
    try:
        return response.json()
    except ValueError:
        return _error("provider_error", "invalid_json")


def _normalize(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    details = row.get("details") or {}
    symbol = str(details.get("ticker") or "").removeprefix("O:")
    option_type = str(details.get("contract_type") or "").lower()
    strike = _number(details.get("strike_price"))
    if not symbol or option_type not in {"call", "put"} or strike is None:
        return None
    quote = row.get("last_quote") or {}
    bid, ask = _number(quote.get("bid")), _number(quote.get("ask"))
    valid_market = bid is not None and bid >= 0 and ask is not None and ask > 0 and ask >= bid
    mid = _number(quote.get("midpoint")) or ((bid + ask) / 2 if valid_market else None)
    spread = ((ask - bid) / mid * 100) if valid_market and mid else None
    greeks = row.get("greeks") or {}
    day = row.get("day") or {}
    timeframe = str(quote.get("timeframe") or (row.get("last_trade") or {}).get("timeframe") or "UNKNOWN").upper()
    quote_time = _nanos(quote.get("last_updated"))
    actionable = timeframe == "REAL-TIME" and valid_market and _is_fresh(quote_time)
    return {
        "contract_symbol": symbol, "strike": strike, "option_type": option_type,
        "expiry": details.get("expiration_date"), "bid": bid, "ask": ask,
        "mid": round(mid, 4) if mid is not None else None,
        "last": _number((row.get("last_trade") or {}).get("price")),
        "volume": int(_number(day.get("volume")) or 0),
        "open_interest": int(_number(row.get("open_interest")) or 0),
        "implied_volatility": _number(row.get("implied_volatility")),
        "delta": _number(greeks.get("delta")), "gamma": _number(greeks.get("gamma")),
        "theta": _number(greeks.get("theta")), "vega": _number(greeks.get("vega")),
        "quote_time": quote_time,
        "last_trade_time": _nanos((row.get("last_trade") or {}).get("sip_timestamp")),
        "spread_pct": round(spread, 1) if spread is not None else None,
        "market_valid": valid_market, "source": "polygon_options",
        "timeframe": timeframe, "delayed": not actionable,
        "actionable": actionable,
    }


def _trusted_page_url(value: str) -> bool:
    page, base = urlparse(value), urlparse(POLYGON_BASE_URL)
    return page.scheme == "https" and page.netloc == base.netloc


def _is_fresh(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds() / 60
        return -2 <= age <= MAX_QUOTE_AGE_MINUTES
    except ValueError:
        return False


def _nanos(value: Any) -> Optional[str]:
    number = _number(value)
    try:
        return datetime.fromtimestamp(number / 1_000_000_000, tz=timezone.utc).isoformat() if number else None
    except (OverflowError, OSError, ValueError):
        return None


def _nearest(rows: list[Dict[str, Any]], spot: Optional[float], limit: int = 12) -> list[Dict[str, Any]]:
    rows.sort(key=lambda row: (abs(row["strike"] - spot), -row["open_interest"]) if spot else (-row["open_interest"],))
    return rows[:limit]


def _ratio(rows: list[Dict[str, Any]], field: str) -> Optional[float]:
    calls = sum(row[field] for row in rows if row["option_type"] == "call")
    puts = sum(row[field] for row in rows if row["option_type"] == "put")
    return puts / calls if calls else None


def _select_expiry(dates: list[str]) -> str:
    today = datetime.now(timezone.utc).date()
    for value in dates:
        if (datetime.strptime(value, "%Y-%m-%d").date() - today).days >= 21:
            return value
    return dates[-1]


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _error(code: str, reason: str) -> Dict[str, Any]:
    return {"error": "provider_unavailable", "error_code": code, "provider_reason": reason, "source": "polygon_options"}
