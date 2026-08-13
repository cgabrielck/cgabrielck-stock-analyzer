import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from backend.config import get_secret


MARKETDATA_API_TOKEN = str(get_secret("MARKETDATA_API_TOKEN") or "").strip()
MARKETDATA_BASE_URL = str(get_secret("MARKETDATA_BASE_URL") or "https://api.marketdata.app/v1").strip().rstrip("/")
MAX_QUOTE_AGE_MINUTES = 2
EASTERN = ZoneInfo("America/New_York")
_ACCEPTED_STATUS = {200, 203}
_COLUMN_FIELDS = (
    "optionSymbol", "underlying", "expiration", "side", "strike", "dte", "updated",
    "bid", "bidSize", "mid", "ask", "askSize", "last", "openInterest", "volume",
    "inTheMoney", "intrinsicValue", "extrinsicValue", "underlyingPrice", "iv",
    "delta", "gamma", "theta", "vega",
)


def is_configured() -> bool:
    return bool(MARKETDATA_API_TOKEN)


def fetch_marketdata_options_chain(
    ticker: str, current_price: Optional[float] = None, deadline: Optional[float] = None,
) -> Dict[str, Any]:
    if not is_configured():
        return _error("not_configured", "api_key_missing")
    symbol = ticker.strip().upper()
    expirations_payload = _get(f"/options/expirations/{symbol}/", {}, deadline)
    if expirations_payload.get("error"):
        return expirations_payload
    expirations = sorted({
        str(value) for value in (expirations_payload.get("expirations") or []) if value
    })
    if not expirations:
        return _error("provider_incomplete", "empty_expirations")
    selected_expiry = _select_expiry(expirations)
    params: Dict[str, Any] = {"expiration": selected_expiry}
    spot = _number(current_price)
    if spot and spot > 0:
        low = round(spot * 0.7, 2)
        high = round(spot * 1.3, 2)
        if low < high:
            params["strike"] = f"{low}-{high}"
    chain_payload = _get(f"/options/chain/{symbol}/", params, deadline)
    if chain_payload.get("error"):
        return chain_payload
    rows = _payload_rows(chain_payload)
    normalized = [_normalize(row) for row in rows]
    normalized = [row for row in normalized if row]
    if not normalized:
        return _error("provider_incomplete", "empty_chain")
    calls = _nearest([row for row in normalized if row["option_type"] == "call"], spot)
    puts = _nearest([row for row in normalized if row["option_type"] == "put"], spot)
    actionable = any(row["actionable"] for row in [*calls, *puts])
    return {
        "ticker": symbol,
        "expirations": expirations,
        "nearest_expiry": expirations[0],
        "selected_expiry": selected_expiry,
        "num_calls": len([row for row in normalized if row["option_type"] == "call"]),
        "num_puts": len([row for row in normalized if row["option_type"] == "put"]),
        "atm_strike": min(
            (row["strike"] for row in normalized), key=lambda value: abs(value - spot)
        ) if spot else None,
        "put_call_ratio": (
            len([row for row in normalized if row["option_type"] == "put"])
            / len([row for row in normalized if row["option_type"] == "call"])
            if any(row["option_type"] == "call" for row in normalized) else None
        ),
        "put_call_volume_ratio": _ratio(normalized, "volume"),
        "put_call_oi_ratio": _ratio(normalized, "open_interest"),
        "calls": calls,
        "puts": puts,
        "source": "marketdata_options",
        "delayed": not actionable,
        "actionable": actionable,
        "partial": False,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "as_of": max((row.get("quote_time") for row in normalized if row.get("quote_time")), default=None),
        "from_cache": False,
    }


def fetch_marketdata_option_contract(underlying: str, contract_symbol: str) -> Dict[str, Any]:
    if not is_configured():
        return _error("not_configured", "api_key_missing")
    symbol = contract_symbol.strip()
    payload = _get(f"/options/quotes/{symbol}/", {}, None)
    if payload.get("error"):
        return payload
    rows = _payload_rows(payload)
    if not rows:
        return _error("provider_incomplete", "contract_not_found")
    contract = _normalize(rows[0])
    if not contract:
        return _error("provider_incomplete", "contract_not_found")
    if contract["contract_symbol"] != symbol:
        return _error("provider_incomplete", "contract_mismatch")
    if underlying.strip().upper() != str(rows[0].get("underlying") or "").upper():
        return _error("provider_incomplete", "contract_mismatch")
    return contract


def _get(path: str, params: Dict[str, Any], deadline: Optional[float]) -> Dict[str, Any]:
    remaining = (deadline - time.monotonic()) if deadline else 7.0
    if remaining <= 0:
        return _error("provider_error", "provider_timeout")
    try:
        response = requests.get(
            f"{MARKETDATA_BASE_URL}{path}",
            params=params,
            headers={"Accept": "application/json", "Authorization": f"Bearer {MARKETDATA_API_TOKEN}"},
            timeout=(min(3.05, remaining), min(7.0, remaining)),
        )
    except requests.RequestException:
        return _error("provider_error", "request_failed")
    if response.status_code == 429:
        return _error("rate_limit", "http_429")
    if response.status_code in {401, 403}:
        return _error("authentication", f"http_{response.status_code}")
    if response.status_code == 204:
        return _error("provider_incomplete", "no_data")
    if response.status_code not in _ACCEPTED_STATUS:
        return _error("provider_error", f"http_{response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        return _error("provider_error", "invalid_json")
    status = str(payload.get("s") or "")
    if status == "no_data":
        return _error("provider_incomplete", "no_data")
    if status == "error":
        return _error("provider_error", "api_error")
    return payload


def _payload_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    columns = {field: payload.get(field) for field in _COLUMN_FIELDS}
    lengths = {
        len(values) for values in columns.values()
        if isinstance(values, list)
    }
    if not lengths:
        return []
    count = max(lengths)
    rows: List[Dict[str, Any]] = []
    for index in range(count):
        row: Dict[str, Any] = {}
        for field, values in columns.items():
            if isinstance(values, list) and index < len(values):
                row[field] = values[index]
            else:
                row[field] = None
        rows.append(row)
    return rows


def _normalize(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    symbol = str(row.get("optionSymbol") or "")
    option_type = str(row.get("side") or "").lower()
    strike = _number(row.get("strike"))
    expiry = _unix_date(row.get("expiration"))
    if not symbol or option_type not in {"call", "put"} or strike is None or expiry is None:
        return None
    bid, ask = _number(row.get("bid")), _number(row.get("ask"))
    valid_market = bid is not None and bid >= 0 and ask is not None and ask > 0 and ask >= bid
    mid = _number(row.get("mid")) or ((bid + ask) / 2 if valid_market else None)
    spread = ((ask - bid) / mid * 100) if valid_market and mid else None
    quote_time = _unix_utc(row.get("updated"))
    actionable = valid_market and _is_fresh(quote_time)
    return {
        "contract_symbol": symbol,
        "strike": strike,
        "option_type": option_type,
        "expiry": expiry,
        "bid": bid,
        "ask": ask,
        "mid": round(mid, 4) if mid is not None else None,
        "last": _number(row.get("last")),
        "volume": int(_number(row.get("volume")) or 0),
        "open_interest": int(_number(row.get("open_interest")) or 0),
        "implied_volatility": _number(row.get("iv")),
        "delta": _number(row.get("delta")),
        "gamma": _number(row.get("gamma")),
        "theta": _number(row.get("theta")),
        "vega": _number(row.get("vega")),
        "quote_time": quote_time,
        "spread_pct": round(spread, 1) if spread is not None else None,
        "market_valid": valid_market,
        "source": "marketdata_options",
        "delayed": not actionable,
        "actionable": actionable,
    }


def _is_fresh(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds() / 60
        return -2 <= age <= MAX_QUOTE_AGE_MINUTES
    except ValueError:
        return False


def _unix_date(value: Any) -> Optional[str]:
    number = _number(value)
    if not number:
        return None
    try:
        return datetime.fromtimestamp(number, tz=EASTERN).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _unix_utc(value: Any) -> Optional[str]:
    number = _number(value)
    if not number:
        return None
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _nearest(rows: list[Dict[str, Any]], spot: Optional[float], limit: int = 12) -> list[Dict[str, Any]]:
    if spot:
        rows.sort(key=lambda row: (abs(row["strike"] - spot), -row["open_interest"], -row["volume"]))
    else:
        rows.sort(key=lambda row: (-row["open_interest"], -row["volume"]))
    return rows[:limit]


def _ratio(rows: list[Dict[str, Any]], field: str) -> Optional[float]:
    calls = sum(row[field] for row in rows if row["option_type"] == "call")
    puts = sum(row[field] for row in rows if row["option_type"] == "put")
    return puts / calls if calls else None


def _select_expiry(dates: list[str]) -> str:
    today = datetime.now(EASTERN).date()
    for value in dates:
        try:
            if (datetime.strptime(value, "%Y-%m-%d").date() - today).days >= 21:
                return value
        except (TypeError, ValueError):
            continue
    return dates[-1]


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if number == number and number not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _error(code: str, reason: str) -> Dict[str, Any]:
    return {
        "error": "rate_limited" if code == "rate_limit" else "provider_unavailable",
        "error_code": code,
        "provider_reason": reason,
        "source": "marketdata_options",
    }
