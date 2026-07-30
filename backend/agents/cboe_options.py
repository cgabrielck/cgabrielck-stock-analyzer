import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import requests


CBOE_OPTIONS_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json"
EASTERN = ZoneInfo("America/New_York")
_OCC_PATTERN = re.compile(r"^([A-Z0-9]{1,6})(\d{6})([CP])(\d{8})$")
_PAYLOAD_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_PAYLOAD_CACHE_LOCK = threading.Lock()
_PAYLOAD_CACHE_TTL_SECONDS = 30


def fetch_cboe_options_chain(ticker: str, current_price: Optional[float] = None) -> Dict[str, Any]:
    """Fetch and normalize Cboe's public delayed option-chain snapshot."""
    symbol = ticker.strip().upper()
    payload_or_error = _fetch_payload(symbol)
    if payload_or_error.get("error"):
        return payload_or_error
    payload = payload_or_error

    data = payload.get("data") or {}
    raw_options = data.get("options") or []
    if not raw_options:
        return _error("provider_incomplete", "empty_chain")
    parsed = [_normalize_contract(row) for row in raw_options]
    contracts = [row for row in parsed if row is not None]
    expirations = sorted({row["expiry"] for row in contracts})
    if not expirations:
        return _error("provider_incomplete", "empty_expirations")

    selected_expiry = _select_expiry(expirations)
    spot = _number(current_price) or _number(data.get("current_price"))
    expiry_contracts = [row for row in contracts if row["expiry"] == selected_expiry]
    calls = _nearest([row for row in expiry_contracts if row["option_type"] == "call"], spot)
    puts = _nearest([row for row in expiry_contracts if row["option_type"] == "put"], spot)
    if not calls and not puts:
        return _error("provider_incomplete", "selected_expiry_empty")

    call_volume = sum(row["volume"] for row in expiry_contracts if row["option_type"] == "call")
    put_volume = sum(row["volume"] for row in expiry_contracts if row["option_type"] == "put")
    call_oi = sum(row["open_interest"] for row in expiry_contracts if row["option_type"] == "call")
    put_oi = sum(row["open_interest"] for row in expiry_contracts if row["option_type"] == "put")
    timestamp = payload.get("timestamp")
    underlying_as_of = data.get("last_trade_time")
    normalized_as_of = _eastern_iso(underlying_as_of or timestamp)
    snapshot_stale = _is_prior_session(normalized_as_of)
    return {
        "ticker": symbol,
        "expirations": expirations,
        "nearest_expiry": expirations[0],
        "selected_expiry": selected_expiry,
        "num_calls": sum(row["option_type"] == "call" for row in expiry_contracts),
        "num_puts": sum(row["option_type"] == "put" for row in expiry_contracts),
        "atm_strike": min(
            (row["strike"] for row in expiry_contracts),
            key=lambda strike: abs(strike - spot),
        ) if spot else None,
        "put_call_ratio": (
            sum(row["option_type"] == "put" for row in expiry_contracts)
            / sum(row["option_type"] == "call" for row in expiry_contracts)
            if any(row["option_type"] == "call" for row in expiry_contracts) else None
        ),
        "put_call_volume_ratio": put_volume / call_volume if call_volume else None,
        "put_call_oi_ratio": put_oi / call_oi if call_oi else None,
        "max_call_oi": _max_strike(expiry_contracts, "call", "open_interest"),
        "max_put_oi": _max_strike(expiry_contracts, "put", "open_interest"),
        "max_call_volume": _max_strike(expiry_contracts, "call", "volume"),
        "max_put_volume": _max_strike(expiry_contracts, "put", "volume"),
        "calls": calls,
        "puts": puts,
        "source": "cboe_delayed_options",
        "delayed": True,
        "timestamp_timezone": "America/New_York_assumed",
        "timestamp_semantics": "provider_snapshot_and_last_trade",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "provider_timestamp": timestamp,
        "as_of": normalized_as_of,
        "snapshot_stale": snapshot_stale,
        "snapshot_warning": "prior_session_snapshot" if snapshot_stale else None,
        "underlying_price": spot,
        "underlying_market": {
            "bid": _number(data.get("bid")), "ask": _number(data.get("ask")),
            "last_trade_time": underlying_as_of,
        },
        "from_cache": False,
    }


def fetch_cboe_option_contract(ticker: str, contract_symbol: str) -> Dict[str, Any]:
    payload = _fetch_payload(ticker.strip().upper())
    if payload.get("error"):
        return payload
    data = payload.get("data") or {}
    for row in data.get("options") or []:
        if str(row.get("option") or "") == contract_symbol:
            contract = _normalize_contract(row)
            if contract is None:
                break
            return {
                **contract,
                "provider_timestamp": payload.get("timestamp"),
                "underlying_last_trade_time": data.get("last_trade_time"),
                "timestamp_timezone": "America/New_York_assumed",
            }
    return _error("provider_incomplete", "contract_not_found")


def _fetch_payload(symbol: str) -> Dict[str, Any]:
    now = time.monotonic()
    with _PAYLOAD_CACHE_LOCK:
        cached = _PAYLOAD_CACHE.get(symbol)
        if cached and now - cached[0] < _PAYLOAD_CACHE_TTL_SECONDS:
            return cached[1]
    try:
        response = requests.get(
            CBOE_OPTIONS_URL.format(ticker=symbol),
            headers={"User-Agent": "stock-analyzer/1.0"},
            timeout=20,
        )
    except requests.RequestException:
        return _error("provider_error", "request_failed")
    if response.status_code == 429:
        return _error("rate_limit", "http_429")
    if not response.ok:
        return _error("provider_error", f"http_{response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        return _error("provider_error", "invalid_json")
    with _PAYLOAD_CACHE_LOCK:
        _PAYLOAD_CACHE[symbol] = (now, payload)
    return payload


def _normalize_contract(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    symbol = str(row.get("option") or "")
    match = _OCC_PATTERN.match(symbol)
    if not match:
        return None
    _, expiry_code, type_code, strike_code = match.groups()
    try:
        expiry = datetime.strptime(expiry_code, "%y%m%d").date().isoformat()
    except ValueError:
        return None
    bid = _number(row.get("bid"))
    ask = _number(row.get("ask"))
    last = _number(row.get("last_trade_price"))
    valid_market = bid is not None and bid >= 0 and ask is not None and ask > 0 and ask >= bid
    mid = (bid + ask) / 2 if valid_market else None
    spread_pct = ((ask - bid) / mid * 100) if mid and bid is not None and ask is not None else None
    return {
        "contract_symbol": symbol,
        "expiry": expiry,
        "option_type": "call" if type_code == "C" else "put",
        "strike": int(strike_code) / 1000,
        "bid": bid,
        "ask": ask,
        "mid": round(mid, 4) if mid is not None else None,
        "last": last,
        "volume": int(_number(row.get("volume")) or 0),
        "open_interest": int(_number(row.get("open_interest")) or 0),
        "implied_volatility": _number(row.get("iv")),
        "delta": _number(row.get("delta")),
        "gamma": _number(row.get("gamma")),
        "theta": _number(row.get("theta")),
        "vega": _number(row.get("vega")),
        "rho": _number(row.get("rho")),
        "theoretical_value": _number(row.get("theo")),
        "last_trade_time": row.get("last_trade_time"),
        "spread_pct": round(spread_pct, 1) if spread_pct is not None else None,
        "source": "cboe_delayed_options",
        "delayed": True,
        "market_valid": valid_market,
    }


def _nearest(contracts: list[Dict[str, Any]], spot: Optional[float], limit: int = 12) -> list[Dict[str, Any]]:
    if spot:
        contracts.sort(key=lambda row: (abs(row["strike"] - spot), -row["open_interest"], -row["volume"]))
    else:
        contracts.sort(key=lambda row: (-row["open_interest"], -row["volume"]))
    return contracts[:limit]


def _select_expiry(expirations: list[str], minimum_days: int = 21) -> str:
    today = datetime.now(EASTERN).date()
    for expiry in expirations:
        if (datetime.strptime(expiry, "%Y-%m-%d").date() - today).days >= minimum_days:
            return expiry
    return expirations[-1]


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if number == number and number not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _max_strike(contracts: list[Dict[str, Any]], option_type: str, field: str) -> Optional[float]:
    candidates = [row for row in contracts if row["option_type"] == option_type and row[field] > 0]
    return max(candidates, key=lambda row: row[field])["strike"] if candidates else None


def _eastern_iso(value: Any) -> Optional[str]:
    if not value:
        return None


def _is_prior_session(value: Optional[str]) -> bool:
    if not value:
        return True
    try:
        return datetime.fromisoformat(value).astimezone(EASTERN).date() < datetime.now(EASTERN).date()
    except ValueError:
        return True
    try:
        timestamp = datetime.fromisoformat(str(value))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=EASTERN)
        return timestamp.isoformat()
    except (TypeError, ValueError):
        return None


def _error(code: str, reason: str) -> Dict[str, Any]:
    return {
        "error": "rate_limited" if code == "rate_limit" else "provider_unavailable",
        "error_code": code,
        "provider_reason": reason,
        "source": "cboe_delayed_options",
    }
