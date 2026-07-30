from datetime import datetime, time, timezone
from typing import Any, Dict, Optional

import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo



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
    if not underlying or not symbol or not expiry:
        return _unavailable(symbol, "contract_identity_missing", trace)
    market_time = current_time.astimezone(EASTERN)
    if market_time.weekday() >= 5 or not (time(9, 30) <= market_time.time() <= time(16, 0)):
        trace.append(_stage("market_session", "failed", warning="option_market_closed"))
        return _unavailable(symbol, "option_market_closed", trace)
    trace.append(_stage("market_session", "done", session="regular"))

    market_state = "UNKNOWN"
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
            return _unavailable(symbol, "contract_not_found", trace)
    except Exception:
        return _unavailable(symbol, "option_chain_unavailable", trace)

    source = "yfinance_option_chain"
    bid = _number(contract.get("bid"))
    ask = _number(contract.get("ask"))
    last = _number(contract.get("lastPrice"))
    quote_time = _timestamp(contract.get("lastTradeDate"))
    volume = int(contract.get("volume") or 0)
    open_interest = int(contract.get("openInterest") or 0)
    implied_volatility = _number(contract.get("impliedVolatility"))
    trace.append(_stage("quote_fetch", "done", source=source))
    trace.append(_stage(
        "freshness", "done" if quote_time else "failed",
        as_of=quote_time.isoformat() if quote_time else None,
        timestamp_semantics="last_trade_proxy_for_quote",
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
        "timestamp_semantics": "last_trade_proxy_for_quote",
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
