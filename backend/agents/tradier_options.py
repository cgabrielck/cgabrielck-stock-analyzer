import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
from config import get_secret


TRADIER_API_TOKEN = str(get_secret("TRADIER_API_TOKEN") or "").strip()
TRADIER_BASE_URL = str(get_secret("TRADIER_BASE_URL") or "https://api.tradier.com/v1").strip().rstrip("/")
MAX_QUOTE_AGE_MINUTES = 2


def is_configured() -> bool:
    return bool(TRADIER_API_TOKEN)


def fetch_tradier_options_chain(
    ticker: str, current_price: Optional[float] = None, deadline: Optional[float] = None,
) -> Dict[str, Any]:
    if not is_configured():
        return _error("not_configured", "token_missing")
    symbol = ticker.strip().upper()
    deadline = deadline or time.monotonic() + 15
    expirations = _get("/markets/options/expirations", {
        "symbol": symbol, "includeAllRoots": "false",
    }, deadline)
    if expirations.get("error"):
        return expirations
    dates = _as_list((expirations.get("expirations") or {}).get("date"))
    if not dates:
        return _error("provider_incomplete", "empty_expirations")
    expiry = _select_expiry([str(value) for value in dates])
    chain = _get("/markets/options/chains", {
        "symbol": symbol, "expiration": expiry, "greeks": "true",
    }, deadline)
    if chain.get("error"):
        return chain
    rows = _as_list((chain.get("options") or {}).get("option"))
    production = "sandbox" not in TRADIER_BASE_URL
    contracts = [_normalize(row, provider_realtime=production) for row in rows if isinstance(row, dict)]
    contracts = [row for row in contracts if row]
    if not contracts:
        return _error("provider_incomplete", "empty_chain")
    spot = _number(current_price)
    calls = _nearest([row for row in contracts if row["option_type"] == "call"], spot)
    puts = _nearest([row for row in contracts if row["option_type"] == "put"], spot)
    actionable = any(row["actionable"] for row in [*calls, *puts])
    return {
        "ticker": symbol,
        "expirations": dates,
        "nearest_expiry": str(dates[0]),
        "selected_expiry": expiry,
        "num_calls": len([row for row in contracts if row["option_type"] == "call"]),
        "num_puts": len([row for row in contracts if row["option_type"] == "put"]),
        "atm_strike": min((row["strike"] for row in contracts), key=lambda value: abs(value - spot)) if spot else None,
        "put_call_ratio": len(puts) / len(calls) if calls else None,
        "put_call_volume_ratio": _ratio(contracts, "volume"),
        "put_call_oi_ratio": _ratio(contracts, "open_interest"),
        "calls": calls,
        "puts": puts,
        "source": "tradier_options",
        "delayed": not actionable,
        "actionable": actionable,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "as_of": max((row.get("quote_time") for row in contracts if row.get("quote_time")), default=None),
        "from_cache": False,
    }


def fetch_tradier_option_contract(contract_symbol: str) -> Dict[str, Any]:
    if not is_configured():
        return _error("not_configured", "token_missing")
    result = _get("/markets/quotes", {"symbols": contract_symbol, "greeks": "true"})
    if result.get("error"):
        return result
    quotes = _as_list((result.get("quotes") or {}).get("quote"))
    if not quotes:
        return _error("provider_incomplete", "contract_not_found")
    contract = _normalize(quotes[0], provider_realtime="sandbox" not in TRADIER_BASE_URL)
    if not contract:
        return _error("provider_incomplete", "invalid_contract")
    if contract["contract_symbol"] != contract_symbol.removeprefix("O:"):
        return _error("provider_incomplete", "contract_mismatch")
    return {
        **contract,
        "source": "tradier_options",
    }


def _get(path: str, params: Dict[str, Any], deadline: Optional[float] = None) -> Dict[str, Any]:
    remaining = (deadline - time.monotonic()) if deadline else 7.0
    if remaining <= 0:
        return _error("provider_error", "provider_timeout")
    try:
        response = requests.get(
            f"{TRADIER_BASE_URL}{path}", params=params,
            headers={"Authorization": f"Bearer {TRADIER_API_TOKEN}", "Accept": "application/json"},
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


def _normalize(row: Dict[str, Any], *, provider_realtime: bool) -> Optional[Dict[str, Any]]:
    symbol = str(row.get("symbol") or "").removeprefix("O:")
    strike = _number(row.get("strike"))
    option_type = str(row.get("option_type") or "").lower()
    if not symbol or strike is None or option_type not in {"call", "put"}:
        return None
    bid, ask = _number(row.get("bid")), _number(row.get("ask"))
    valid_market = bid is not None and bid >= 0 and ask is not None and ask > 0 and ask >= bid
    mid = (bid + ask) / 2 if valid_market else None
    spread = ((ask - bid) / mid * 100) if mid else None
    greeks = row.get("greeks") or {}
    quote_ms = max(int(_number(row.get("bid_date")) or 0), int(_number(row.get("ask_date")) or 0))
    try:
        quote_time = datetime.fromtimestamp(quote_ms / 1000, tz=timezone.utc).isoformat() if quote_ms else None
    except (OverflowError, OSError, ValueError):
        quote_time = None
    actionable = provider_realtime and valid_market and _is_fresh(quote_time)
    return {
        "contract_symbol": symbol, "strike": strike, "option_type": option_type,
        "expiry": row.get("expiration_date"), "bid": bid, "ask": ask,
        "mid": round(mid, 4) if mid is not None else None,
        "last": _number(row.get("last")), "volume": int(_number(row.get("volume")) or 0),
        "open_interest": int(_number(row.get("open_interest")) or 0),
        "implied_volatility": _number(greeks.get("mid_iv") or greeks.get("smv_vol")),
        "delta": _number(greeks.get("delta")), "gamma": _number(greeks.get("gamma")),
        "theta": _number(greeks.get("theta")), "vega": _number(greeks.get("vega")),
        "rho": _number(greeks.get("rho")), "quote_time": quote_time,
        "last_trade_time": _epoch_ms(row.get("trade_date")),
        "spread_pct": round(spread, 1) if spread is not None else None,
        "market_valid": valid_market, "source": "tradier_options",
        "delayed": not actionable, "actionable": actionable,
    }


def _is_fresh(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds() / 60
        return -2 <= age <= MAX_QUOTE_AGE_MINUTES
    except ValueError:
        return False


def _epoch_ms(value: Any) -> Optional[str]:
    number = _number(value)
    try:
        return datetime.fromtimestamp(number / 1000, tz=timezone.utc).isoformat() if number else None
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


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _error(code: str, reason: str) -> Dict[str, Any]:
    return {"error": "provider_unavailable", "error_code": code, "provider_reason": reason, "source": "tradier_options"}
