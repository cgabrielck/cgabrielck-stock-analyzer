from datetime import datetime, time, timezone
from typing import Any, Dict, Optional

import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo

from agents.tradier_options import fetch_tradier_option_contract
from agents.polygon_options import fetch_polygon_option_contract



MAX_OPTION_QUOTE_AGE_MINUTES = 20
MAX_OPTION_SPREAD_PCT = 20.0
EASTERN = ZoneInfo("America/New_York")


def fetch_option_quote(
    underlying: str,
    rule_data: Dict[str, Any],
    event_type: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return a conservative executable-side quote with an auditable stage trace."""
    current_time = now or datetime.now(timezone.utc)
    symbol = str(rule_data.get("monitor_symbol") or "")
    expiry = str(rule_data.get("expiry") or "")
    trace = []
    side = str(rule_data.get("option_type") or "unknown")
    if not underlying or not symbol or not expiry:
        return _unavailable(symbol, "contract_identity_missing", trace)
    market_time = current_time.astimezone(EASTERN)
    if market_time.weekday() >= 5 or not (time(9, 30) <= market_time.time() <= time(16, 0)):
        trace.append(_stage("market_session", "failed", warning="option_market_closed"))
        return _unavailable(symbol, "option_market_closed", trace)
    trace.append(_stage("market_session", "done", session="regular"))

    market_state = "UNKNOWN"
    provider_contract = None
    try:
        stock = yf.Ticker(underlying)
        market_state = str((stock.info or {}).get("marketState") or "").upper()
        if market_state != "REGULAR":
            trace[-1] = _stage("market_session", "failed", warning="option_market_not_regular", market_state=market_state or "UNKNOWN")
            return _unavailable(symbol, "option_market_not_regular", trace)
        chain = stock.option_chain(expiry)
        contract = _find_contract(chain.calls, symbol)
        side = "call"
        if contract is None:
            contract = _find_contract(chain.puts, symbol)
            side = "put"
        if contract is None:
            raise LookupError("contract_not_found")
    except Exception:
        if market_state != "REGULAR":
            trace[-1] = _stage(
                "market_session", "failed", warning="option_market_state_unverified",
                market_state=market_state or "UNKNOWN",
            )
            return _unavailable(symbol, "option_market_state_unverified", trace)
        source, provider_contract = _fetch_provider_contract(underlying, symbol)
        if provider_contract is None:
            return _unavailable(symbol, "actionable_option_quote_unavailable", trace)

    if provider_contract is None:
        source = "yfinance_option_chain"
        bid = _number(contract.get("bid"))
        ask = _number(contract.get("ask"))
        last = _number(contract.get("lastPrice"))
        quote_time = _timestamp(contract.get("lastTradeDate"))
        volume = int(contract.get("volume") or 0)
        open_interest = int(contract.get("openInterest") or 0)
        implied_volatility = _number(contract.get("impliedVolatility"))
        timestamp_semantics = "last_trade_proxy_for_quote"
    else:
        bid = _number(provider_contract.get("bid"))
        ask = _number(provider_contract.get("ask"))
        last = _number(provider_contract.get("last"))
        quote_time = _timestamp(provider_contract.get("quote_time"))
        volume = int(provider_contract.get("volume") or 0)
        open_interest = int(provider_contract.get("open_interest") or 0)
        implied_volatility = _number(provider_contract.get("implied_volatility"))
        side = provider_contract.get("option_type") or side
        timestamp_semantics = "bid_ask_quote_time"
    if source == "yfinance_option_chain" and _yahoo_quote_unusable(bid, ask, quote_time, current_time):
        fallback_source, fallback_contract = _fetch_provider_contract(underlying, symbol)
        if fallback_contract is not None:
            source = fallback_source
            bid = _number(fallback_contract.get("bid"))
            ask = _number(fallback_contract.get("ask"))
            last = _number(fallback_contract.get("last"))
            quote_time = _timestamp(fallback_contract.get("quote_time"))
            volume = int(fallback_contract.get("volume") or 0)
            open_interest = int(fallback_contract.get("open_interest") or 0)
            implied_volatility = _number(fallback_contract.get("implied_volatility"))
            side = fallback_contract.get("option_type") or side
            timestamp_semantics = "bid_ask_quote_time"
    trace.append(_stage("quote_fetch", "done", source=source, timestamp_semantics=timestamp_semantics))
    trace.append(_stage(
        "freshness", "done" if quote_time else "failed",
        as_of=quote_time.isoformat() if quote_time else None,
        timestamp_semantics=timestamp_semantics,
        warning=None if quote_time else "last_trade_time_missing",
    ))
    if quote_time is None:
        return _unavailable(symbol, "last_trade_time_missing", trace, bid=bid, ask=ask, last=last)
    age_minutes = (current_time - quote_time).total_seconds() / 60
    if age_minutes < -2 or age_minutes > MAX_OPTION_QUOTE_AGE_MINUTES:
        trace[-1] = _stage("freshness", "failed", as_of=quote_time.isoformat(), warning="stale_option_trade")
        return _unavailable(
            symbol, "stale_option_trade", trace, bid=bid, ask=ask, last=last,
            quote_time=quote_time, age_minutes=age_minutes,
        )

    mid = (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid else None
    spread_pct = ((ask - bid) / mid * 100) if mid and bid is not None and ask is not None else None
    is_entry = event_type == "option_entry"
    if not is_entry and (bid is None or bid <= 0):
        trace.append(_stage("liquidity", "failed", warning="zero_bid_exit", spread_pct=spread_pct))
        return _unavailable(
            symbol, "zero_bid_exit", trace, bid=bid, ask=ask, last=last,
            quote_time=quote_time, age_minutes=age_minutes, spread_pct=spread_pct,
        )
    liquidity_ok = bid is not None and bid >= 0 and ask is not None and ask > 0 and ask >= bid
    if spread_pct is not None and spread_pct > MAX_OPTION_SPREAD_PCT:
        liquidity_ok = False
    trace.append(_stage(
        "liquidity", "done" if liquidity_ok else "failed",
        warning=None if liquidity_ok else "non_executable_market",
        spread_pct=round(spread_pct, 1) if spread_pct is not None else None,
    ))
    if not liquidity_ok:
        return _unavailable(
            symbol, "non_executable_market", trace, bid=bid, ask=ask, last=last,
            quote_time=quote_time, age_minutes=age_minutes, spread_pct=spread_pct,
        )

    executable_price = ask if is_entry else bid
    trace.append(_stage("executable_price", "done", side="ask" if is_entry else "bid"))

    return {
        "available": True,
        "price": round(float(executable_price), 4),
        "bid": bid,
        "ask": ask,
        "mid": round(mid, 4) if mid is not None else None,
        "last": last,
        "quote_time": quote_time.isoformat(),
        "retrieved_at": current_time.isoformat(),
        "age_minutes": round(age_minutes, 2),
        "spread_pct": round(spread_pct, 1) if spread_pct is not None else None,
        "source": source,
        "delayed": False,
        "session": "option_market",
        "market_state": market_state,
        "timestamp_semantics": timestamp_semantics,
        "stale": False,
        "contract_symbol": symbol,
        "option_type": side,
        "volume": volume,
        "open_interest": open_interest,
        "implied_volatility": implied_volatility,
        "agent_trace": trace,
    }


def _find_contract(frame: Any, symbol: str) -> Optional[pd.Series]:
    if frame is None or frame.empty or "contractSymbol" not in frame.columns:
        return None
    rows = frame[frame["contractSymbol"].astype(str) == symbol]
    return rows.iloc[0] if not rows.empty else None


def _fetch_provider_contract(underlying: str, symbol: str) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    for provider, fetcher, args in (
        ("tradier_options", fetch_tradier_option_contract, (symbol,)),
        ("polygon_options", fetch_polygon_option_contract, (underlying, symbol)),
    ):
        try:
            candidate = fetcher(*args)
        except Exception:
            continue
        if not candidate.get("error") and candidate.get("actionable"):
            return provider, candidate
    return None, None


def _yahoo_quote_unusable(
    bid: Optional[float], ask: Optional[float], quote_time: Optional[datetime], current_time: datetime,
) -> bool:
    if bid is None or bid < 0 or ask is None or ask <= 0 or ask < bid or quote_time is None:
        return True
    age_minutes = (current_time - quote_time).total_seconds() / 60
    return age_minutes < -2 or age_minutes > MAX_OPTION_QUOTE_AGE_MINUTES


def _unavailable(symbol: str, reason: str, trace: list, **values: Any) -> Dict[str, Any]:
    return {
        "available": False,
        "price": None,
        "contract_symbol": symbol,
        "stale": True,
        "stale_reason": reason,
        "agent_trace": trace,
        **{
            key: value.isoformat() if key == "quote_time" and isinstance(value, datetime) else value
            for key, value in values.items()
        },
    }


def _stage(stage: str, status: str, **details: Any) -> Dict[str, Any]:
    return {"stage": stage, "status": status, **details}


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if number == number and number not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any, assumed_timezone: ZoneInfo = ZoneInfo("UTC")) -> Optional[datetime]:
    try:
        timestamp = pd.Timestamp(value)
        if pd.isna(timestamp):
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(assumed_timezone)
        return timestamp.to_pydatetime().astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None
