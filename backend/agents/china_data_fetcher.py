import time as _time
from typing import Any, Dict, Optional

import requests

_SESSION = requests.Session()
_SESSION.headers.update({"Referer": "https://finance.sina.com.cn"})

_SINA_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_EM_FIN_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_EM_BS_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
QUOTE_CACHE_TTL_SECONDS = 60
FUNDAMENTALS_CACHE_TTL_SECONDS = 6 * 60 * 60


def _ak():
    """Load akshare on first East Money call.

    Importing akshare pulls py_mini_racer, which still calls the deprecated
    pkg_resources API. US research does not need that import at startup.
    """
    import akshare as ak

    return ak


def _get_cached(
    store: Dict[str, tuple[float, Dict[str, Any]]], ticker: str, ttl: int,
) -> Optional[Dict[str, Any]]:
    entry = store.get(ticker)
    if entry and _time.time() - entry[0] < ttl:
        return dict(entry[1])
    store.pop(ticker, None)
    return None


def clear_provider_cache(ticker: Optional[str] = None) -> None:
    """Clear provider-local caches when a user explicitly requests live data."""
    stores = (_SINA_CACHE, _EM_FIN_CACHE, _EM_BS_CACHE)
    for store in stores:
        if ticker:
            store.pop(ticker, None)
        else:
            store.clear()


def fetch_sina_quote(ticker: str) -> Optional[Dict[str, Any]]:
    cached = _get_cached(_SINA_CACHE, ticker, QUOTE_CACHE_TTL_SECONDS)
    if cached:
        return cached
    try:
        url = f"https://hq.sinajs.cn/list=gb_{ticker.lower()}"
        resp = _SESSION.get(url, timeout=10)
        text = resp.text.strip()
        if not text or "=" not in text or '"' not in text:
            return None
        data = text.split('"')[1].split(",")
        result = {
            "price": float(data[1]),
            "open": float(data[5]),
            "high": float(data[6]),
            "low": float(data[7]),
            "prev_close": float(data[8]),
            "volume": int(float(data[10])),
            "market_cap": float(data[12]),
            "pe_ratio": float(data[14]),
        }
        _SINA_CACHE[ticker] = (_time.time(), result)
        return result
    except Exception:
        return None


def fetch_em_financials(ticker: str) -> Optional[Dict[str, Any]]:
    cached = _get_cached(_EM_FIN_CACHE, ticker, FUNDAMENTALS_CACHE_TTL_SECONDS)
    if cached:
        return cached
    try:
        df = _ak().stock_financial_us_analysis_indicator_em(symbol=ticker)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        result: Dict[str, Any] = {}
        if "OPERATE_INCOME_YOY" in df.columns and row["OPERATE_INCOME_YOY"] is not None:
            result["revenue_growth"] = float(row["OPERATE_INCOME_YOY"])
        if "BASIC_EPS_YOY" in df.columns and row["BASIC_EPS_YOY"] is not None:
            result["eps_growth"] = float(row["BASIC_EPS_YOY"])
        if "NET_PROFIT_RATIO" in df.columns and row["NET_PROFIT_RATIO"] is not None:
            result["profit_margin"] = float(row["NET_PROFIT_RATIO"])
        if "ROE_AVG" in df.columns and row["ROE_AVG"] is not None:
            result["roe"] = float(row["ROE_AVG"])
        if "OPERATE_INCOME" in df.columns and row["OPERATE_INCOME"] is not None:
            result["revenue_ttm"] = float(row["OPERATE_INCOME"])
        if "PARENT_HOLDER_NETPROFIT" in df.columns and row["PARENT_HOLDER_NETPROFIT"] is not None:
            result["net_income"] = float(row["PARENT_HOLDER_NETPROFIT"])
        if "PARENT_HOLDER_NETPROFIT_YOY" in df.columns and row["PARENT_HOLDER_NETPROFIT_YOY"] is not None:
            result["net_income_growth"] = float(row["PARENT_HOLDER_NETPROFIT_YOY"])
        if "BASIC_EPS" in df.columns and row["BASIC_EPS"] is not None:
            result["eps"] = float(row["BASIC_EPS"])
        if not result:
            return None
        _EM_FIN_CACHE[ticker] = (_time.time(), result)
        return result
    except Exception:
        return None


def fetch_em_balance_sheet(ticker: str) -> Optional[Dict[str, Any]]:
    cached = _get_cached(_EM_BS_CACHE, ticker, FUNDAMENTALS_CACHE_TTL_SECONDS)
    if cached:
        return cached
    try:
        df = _ak().stock_financial_us_report_em(
            stock=ticker, symbol="资产负债表", indicator="年报"
        )
        if df is None or df.empty:
            return None
        debt_series = df[df["ITEM_NAME"] == "总负债"]
        equity_series = df[df["ITEM_NAME"] == "归属于母公司股东权益"]
        if debt_series.empty or equity_series.empty:
            equity_series = df[df["ITEM_NAME"] == "股东权益合计"]
        if debt_series.empty or equity_series.empty:
            return None
        total_debt = float(debt_series.iloc[0]["AMOUNT"])
        total_equity = float(equity_series.iloc[0]["AMOUNT"])
        result = {
            "debt_equity": round(total_debt / total_equity, 2) if total_equity else None,
        }
        _EM_BS_CACHE[ticker] = (_time.time(), result)
        return result
    except Exception:
        return None


def fetch_china_data(ticker: str) -> Optional[Dict[str, Any]]:
    quote = fetch_sina_quote(ticker)
    if quote is None:
        return None
    fin = fetch_em_financials(ticker)
    bs = fetch_em_balance_sheet(ticker)
    result: Dict[str, Any] = {
        "ticker": ticker,
        "price": quote["price"],
        "pe_ratio": quote.get("pe_ratio"),
        "market_cap": quote.get("market_cap"),
        "fifty_two_week_high": quote.get("high"),
        "fifty_two_week_low": quote.get("low"),
        "price_session": "sinajs",
    }
    if fin:
        result.update(fin)
    if bs:
        result.update(bs)
    return result


def fetch_china_daily(ticker: str) -> Optional[list]:
    try:
        df = _ak().stock_us_daily(symbol=ticker, adjust="qfq")
        if df is None or df.empty:
            return None
        return df.tail(100).to_dict("records")
    except Exception:
        return None
