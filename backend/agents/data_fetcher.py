import concurrent.futures
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
import re
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

import yfinance as yf
import pandas as pd
import requests

from utils.constants import STOCK_UNIVERSE, SEC_HEADERS, DATA_DIR
from utils.cache import cache
from utils.price_utils import get_latest_price, get_latest_quote, get_session_ranges
from agents.auto_upgrader import agent_state
from agents.sec_analyzer import ticker_to_cik
from agents.alpha_vantage_data import fetch_fundamentals as fetch_alpha_fundamentals
from agents.alpha_vantage_data import is_configured as alpha_vantage_configured
from agents.alpha_vantage_data import fetch_global_quote as fetch_alpha_global_quote
from agents.cboe_options import fetch_cboe_options_chain
try:
    from agents.china_data_fetcher import clear_provider_cache, fetch_china_data, fetch_china_daily
except ImportError:
    clear_provider_cache = None
    fetch_china_data = None
    fetch_china_daily = None

_REQUEST_SESSION: requests.Session = requests.Session()
_REQUEST_SESSION.headers.update(SEC_HEADERS)
_REQUEST_SESSION.headers["User-Agent"] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "KHTML, like Gecko Chrome/120.0.0.0 Safari/537.36"
)

try:
    from yfinance.utils import YfConfig
    YfConfig.session = _REQUEST_SESSION
except Exception:
    pass

_SEED_DATA: Dict[str, Any] = {}
try:
    from agents.seed_data import SEED_DATA as _SD
    _SEED_DATA = _SD
except Exception:
    _seed_path = os.path.join(DATA_DIR, "seed_data.json")
    if os.path.exists(_seed_path):
        try:
            with open(_seed_path) as _f:
                _SEED_DATA = json.load(_f)
        except Exception:
            pass

REQUEST_TIMEOUT: int = 30
MAX_WORKERS: int = 3
EASTERN = ZoneInfo("America/New_York")
NEWS_MAX_AGE_DAYS = 7
NEWS_DISTINCTIVE_ALIASES = {
    "V": ["Visa"], "MA": ["Mastercard"], "LI": ["Li Auto"],
    "GE": ["GE Aerospace", "General Electric"], "HD": ["Home Depot"],
}


import time as _time


def _now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


SCORING_KEYS: list[str] = ["revenue_growth", "eps_growth", "profit_margin", "peg", "roe", "debt_equity"]


def _count_available_metrics(data: Dict[str, Any]) -> int:
    count = 0
    for key in SCORING_KEYS:
        value = data.get(key)
        try:
            valid = value is not None and float(value) == float(value)
        except (TypeError, ValueError):
            valid = False
        if key == "peg":
            valid = valid and float(value) > 0
        count += int(valid)
    return count


def _seed_as_of(seed: Dict[str, Any]) -> Optional[str]:
    value = seed.get("snapshot_as_of") or seed.get("fetched_at")
    if not value:
        return None
    try:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        return timestamp.tz_convert("UTC").isoformat()
    except (TypeError, ValueError):
        return None


def _data_quality(
    data: Dict[str, Any],
    source: str,
    source_type: str,
    *,
    from_cache: bool = False,
    is_fallback: bool = False,
    stale: bool = False,
    as_of: Optional[str] = None,
    components: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "source": source,
        "source_type": source_type,
        "from_cache": from_cache,
        "is_fallback": is_fallback,
        "stale": stale,
        "fetched_at": data.get("fetched_at") or _now_str(),
        "as_of": as_of,
        "metrics_available": _count_available_metrics(data),
        "metrics_total": len(SCORING_KEYS),
        "source_components": components or [],
    }


def get_seed_fallback(ticker: str, reason: str = "provider_timeout") -> Optional[Dict[str, Any]]:
    seed = _SEED_DATA.get(ticker)
    if not seed:
        return None
    result = dict(seed)
    snapshot_as_of = _seed_as_of(result)
    result.update({
        "ticker": ticker,
        "snapshot_as_of": snapshot_as_of,
        "fetched_at": _now_str(),
        "data_source": "seed_data",
        "fallback_reason": reason,
    })
    result["data_quality"] = _data_quality(
        result,
        "seed_data",
        "seed",
        is_fallback=True,
        stale=True,
        as_of=snapshot_as_of,
        components=[{
            "source": "seed_data", "role": "fundamentals",
            "as_of": snapshot_as_of, "stale": True, "fallback_reason": reason,
        }],
    )
    return result


def fetch_stock_data(ticker: str, force_refresh: bool = False) -> Dict[str, Any]:
    if not force_refresh:
        cached = cache.get(ticker, "info")
        if cached:
            cached = dict(cached)
            previous_quality = cached.get("data_quality", {})
            cached["data_quality"] = _data_quality(
                cached,
                cached.get("data_source", "cache"),
                previous_quality.get("source_type", "live"),
                from_cache=True,
                is_fallback=previous_quality.get("is_fallback", False),
                stale=previous_quality.get("stale", cached.get("price_stale", False)),
                as_of=previous_quality.get("as_of") or cached.get("price_quote_time"),
                components=previous_quality.get("source_components", []),
            )
            return cached

    if force_refresh and clear_provider_cache:
        clear_provider_cache(ticker)

    last_error: Optional[str] = None
    alpha_executor = None
    alpha_future = None
    alpha_fund = None
    if alpha_vantage_configured():
        alpha_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        alpha_future = alpha_executor.submit(fetch_alpha_fundamentals, ticker, force_refresh)

    def resolve_alpha() -> Optional[Dict[str, Any]]:
        nonlocal alpha_executor, alpha_future, alpha_fund
        if alpha_future is not None:
            try:
                alpha_fund = alpha_future.result(timeout=20)
            except Exception:
                alpha_fund = None
            finally:
                if alpha_executor is not None:
                    alpha_executor.shutdown(wait=False, cancel_futures=True)
                alpha_executor = None
                alpha_future = None
        return alpha_fund

    for attempt in range(3):
        try:
            stock = yf.Ticker(ticker, session=_REQUEST_SESSION)
            info = stock.info
            if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
                price_data = _fetch_price_fallback(ticker)
                if price_data:
                    info.update(price_data)

            financials = _safe_df(stock.financials)
            balance_sheet = _safe_df(stock.balance_sheet)
            cashflow = _safe_df(stock.cashflow)

            cik = info.get("cik")
            if cik is None:
                cik = ticker_to_cik(ticker)

            recommendation_mean = info.get("recommendationMean")
            if recommendation_mean is not None:
                rating_map = {1: "Strong Buy", 2: "Buy", 3: "Hold", 4: "Underperform", 5: "Sell"}
                rating_label = rating_map.get(round(recommendation_mean), "N/A")
            else:
                rating_label = None

            latest_quote = get_latest_quote(stock, fallback_close=info.get("price"), info=info)
            latest_price = latest_quote["price"]
            price_session = latest_quote["session"]

            result: Dict[str, Any] = {
                "ticker": ticker,
                "fetched_at": _now_str(),
                "data_source": "yfinance",
                "price": latest_price,
                "price_session": price_session,
                "price_source": latest_quote["source"],
                "price_quote_time": latest_quote["quote_time"],
                "price_market_state": latest_quote["market_state"],
                "price_stale": latest_quote["stale"],
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector") or next(
                    (s["sector"] for s in STOCK_UNIVERSE if s["ticker"] == ticker), None
                ),
                "industry": info.get("industry"),
                "longName": info.get("longName"),
                "cik": cik,
                "peg": info.get("pegRatio"),
                "ps_ratio": info.get("priceToSalesTrailing12Months"),
                "pb_ratio": info.get("priceToBook"),
                "ev_ebitda": info.get("enterpriseToEbitda"),
                "dividend_yield": info.get("dividendYield"),
                "short_ratio": info.get("shortRatio"),
                "beta": info.get("beta"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "target_mean_price": info.get("targetMeanPrice"),
                "target_high_price": info.get("targetHighPrice"),
                "target_low_price": info.get("targetLowPrice"),
                "recommendation_mean": recommendation_mean,
                "rating_label": rating_label,
                "number_of_analysts": info.get("numberOfAnalystOpinions"),
                "held_percent_institutions": info.get("heldPercentInstitutions"),
            }

            if financials is not None:
                result.update(_extract_financials(financials, balance_sheet, cashflow))

            alpha_fund = resolve_alpha()
            alpha_meta = {}
            if alpha_fund:
                alpha_meta = alpha_fund.get("_alpha_meta", {})
                for key, value in alpha_fund.items():
                    if not key.startswith("_") and key not in {"ticker", "sector"}:
                        result[key] = value
                result["data_source"] = "alpha_vantage+yfinance"

            try:
                esg_data = stock.sustainability
                if esg_data is not None and not esg_data.empty:
                    result["esg_score"] = float(esg_data.loc["totalEsg", "realValue"]) if "totalEsg" in esg_data.index else None
            except Exception as e:
                agent_state.log_source_result(f"esg:{ticker}", False, str(e))

            try:
                insider = stock.insider_transactions
                if insider is not None and not insider.empty:
                    net = insider["shares"].sum() if "shares" in insider.columns else 0
                    result["insider_net_shares"] = int(net)
            except Exception as e:
                agent_state.log_source_result(f"insider:{ticker}", False, str(e))

            components = [{"source": "yfinance", "role": "quote_and_supplemental_data" if alpha_fund else "fundamentals_and_quote"}]
            if alpha_fund:
                components.insert(0, {
                    "source": "alpha_vantage", "role": "fundamentals",
                    "endpoints": alpha_meta.get("endpoints", []), "as_of": alpha_meta.get("as_of"),
                })
            result["data_quality"] = _data_quality(
                result, result["data_source"], "hybrid" if alpha_fund else "live",
                stale=bool(result.get("price_stale")),
                as_of=alpha_meta.get("as_of") if alpha_fund else result.get("price_quote_time") or result["fetched_at"],
                components=components,
            )

            if alpha_fund:
                agent_state.log_source_result(f"alpha_vantage:{ticker}", True)
            agent_state.log_source_result(f"yfinance:{ticker}", True)
            cache.set(ticker, "info", result)
            return result

        except Exception as e:
            last_error = str(e)
            agent_state.log_source_result(f"yfinance:{ticker}", False, last_error)
            if attempt < 2:
                _time.sleep(2 ** attempt)

    alpha_fund = resolve_alpha()
    if alpha_fund:
        result = {key: value for key, value in alpha_fund.items() if not key.startswith("_")}
        alpha_meta = alpha_fund.get("_alpha_meta", {})
        quote = _fetch_price_fallback(ticker) or fetch_alpha_global_quote(ticker, force_refresh=force_refresh)
        if quote:
            result.update(quote)
        result.update({
            "ticker": ticker,
            "cik": ticker_to_cik(ticker),
            "fetched_at": _now_str(),
            "data_source": "alpha_vantage+yahoo_chart" if quote and quote.get("_fallback") else "alpha_vantage",
            "sector": next((s["sector"] for s in STOCK_UNIVERSE if s["ticker"] == ticker), result.get("sector")),
        })
        components = [{
            "source": "alpha_vantage", "role": "fundamentals",
            "endpoints": alpha_meta.get("endpoints", []), "as_of": alpha_meta.get("as_of"),
        }]
        if quote:
            components.append({"source": "yahoo_chart" if quote.get("_fallback") else "alpha_vantage", "role": "quote"})
        result["data_quality"] = _data_quality(
            result, result["data_source"], "hybrid" if len(components) > 1 else "live",
            is_fallback=True, stale=bool(result.get("price_stale", True)),
            as_of=alpha_meta.get("as_of"), components=components,
        )
        cache.set(ticker, "info", result)
        agent_state.log_source_result(f"alpha_vantage:{ticker}", True)
        return result

    china = fetch_china_data(ticker) if fetch_china_data else None
    if china:
        china["sector"] = next(
            (s["sector"] for s in STOCK_UNIVERSE if s["ticker"] == ticker), None
        )
        china["cik"] = ticker_to_cik(ticker)
        china["fetched_at"] = _now_str()
        china["data_source"] = "sina_eastmoney"
        components = [{"source": "sina_eastmoney", "role": "quote_and_cn_fundamentals"}]
        seed_used = False
        yahoo_fund = _fetch_yahoo_fundamentals(ticker)
        if yahoo_fund:
            for k, v in yahoo_fund.items():
                if k != "_source" and k not in china:
                    china[k] = v
            components.append({"source": "yahoo_quote_summary", "role": "fundamentals"})
        else:
            seed = _SEED_DATA.get(ticker)
            if seed:
                for k in ("revenue_growth", "eps_growth", "profit_margin", "peg",
                          "roe", "debt_equity", "revenue_ttm", "net_income",
                          "net_income_growth", "eps", "beta", "dividend_yield",
                          "target_mean_price", "fifty_two_week_high",
                          "fifty_two_week_low", "longName", "insider_net_shares",
                          "esg_score", "recommendation_mean", "rating_label",
                          "number_of_analysts", "held_percent_institutions",
                          "ps_ratio", "pb_ratio", "ev_ebitda", "short_ratio",
                          "target_high_price", "target_low_price", "forward_pe"):
                    if k not in china and k in seed:
                        china[k] = seed[k]
                seed_used = True
                components.append({"source": "seed_data", "role": "fundamentals", "as_of": _seed_as_of(seed), "stale": True})
        china["data_source"] = "+".join(component["source"] for component in components)
        china["data_quality"] = _data_quality(
            china, china["data_source"], "hybrid" if len(components) > 1 else "live",
            is_fallback=True, stale=seed_used, as_of=china.get("price_quote_time") or china["fetched_at"],
            components=components,
        )
        agent_state.log_source_result(f"china:{ticker}", True)
        return china

    yahoo_fund = _fetch_yahoo_fundamentals(ticker)
    if yahoo_fund:
        chart = _fetch_price_fallback(ticker)
        price = chart.get("price") if chart else yahoo_fund.get("price")
        yahoo_fund["ticker"] = ticker
        yahoo_fund["cik"] = ticker_to_cik(ticker)
        yahoo_fund["fetched_at"] = _now_str()
        yahoo_fund["data_source"] = "yahoo_quote_summary+yahoo_chart" if chart else "yahoo_quote_summary"
        yahoo_fund["sector"] = next(
            (s["sector"] for s in STOCK_UNIVERSE if s["ticker"] == ticker), None
        )
        if price:
            yahoo_fund["price"] = price
        components = [{"source": "yahoo_quote_summary", "role": "fundamentals"}]
        if chart:
            components.append({"source": "yahoo_chart", "role": "quote"})
        yahoo_fund["data_quality"] = _data_quality(
            yahoo_fund, yahoo_fund["data_source"], "hybrid" if chart else "live",
            is_fallback=True, as_of=yahoo_fund["fetched_at"], components=components,
        )
        del yahoo_fund["_source"]
        agent_state.log_source_result(f"yahoo:{ticker}", True)
        return yahoo_fund

    fallback = _fetch_price_fallback(ticker)
    if fallback:
        cik = ticker_to_cik(ticker)
        fallback["ticker"] = ticker
        fallback["cik"] = cik
        fallback["fetched_at"] = _now_str()
        fallback["data_source"] = "yahoo_price_seed_fundamentals" if _SEED_DATA.get(ticker) else "yahoo_price_only"
        fallback["sector"] = next(
            (s["sector"] for s in STOCK_UNIVERSE if s["ticker"] == ticker), None
        )
        seed = _SEED_DATA.get(ticker)
        if seed:
            for k in ("revenue_growth", "eps_growth", "profit_margin", "peg",
                      "roe", "debt_equity", "longName"):
                if k not in fallback and k in seed:
                    fallback[k] = seed[k]
        components = [{"source": "yahoo_chart", "role": "quote"}]
        if seed:
            components.append({"source": "seed_data", "role": "fundamentals", "as_of": _seed_as_of(seed), "stale": True})
        fallback["data_quality"] = _data_quality(
            fallback, fallback["data_source"], "hybrid" if seed else "price_only",
            is_fallback=True, stale=bool(seed),
            as_of=_seed_as_of(seed) if seed else fallback["fetched_at"], components=components,
        )
        agent_state.log_source_result(f"fallback:{ticker}", True)
        return fallback

    seed = _SEED_DATA.get(ticker)
    if seed:
        seed = dict(seed)
        snapshot_as_of = _seed_as_of(seed)
        seed["snapshot_as_of"] = snapshot_as_of
        seed["fetched_at"] = _now_str()
        seed["data_source"] = "seed_data"
        seed["data_quality"] = _data_quality(
            seed, "seed_data", "seed", is_fallback=True, stale=True, as_of=snapshot_as_of,
            components=[{"source": "seed_data", "role": "fundamentals_and_quote", "as_of": snapshot_as_of, "stale": True}],
        )
        agent_state.log_source_result(f"seed:{ticker}", True)
        return seed

    error = {"ticker": ticker, "error": last_error or "unknown", "sector": None, "fetched_at": _now_str(), "data_source": "unavailable"}
    error["data_quality"] = _data_quality(error, "unavailable", "error", is_fallback=True, stale=True)
    return error


def _safe_df(df: Any) -> Optional[Any]:
    if df is None or df.empty:
        return None
    return df


def _extract_financials(financials: Any, balance_sheet: Any, cashflow: Any) -> Dict[str, float]:
    result: Dict[str, float] = {}
    try:
        if "Total Revenue" in financials.index:
            rev = financials.loc["Total Revenue"].dropna()
            if len(rev) >= 2:
                result["revenue_growth"] = float((rev.iloc[0] / rev.iloc[1] - 1) * 100)
            if len(rev) >= 1:
                result["revenue_ttm"] = float(rev.iloc[0])
    except Exception:
        pass

    try:
        if "Net Income" in financials.index:
            ni = financials.loc["Net Income"].dropna()
            if len(ni) >= 1:
                result["net_income"] = float(ni.iloc[0])
            if len(ni) >= 2:
                result["net_income_growth"] = float((ni.iloc[0] / ni.iloc[1] - 1) * 100)
    except Exception:
        pass

    try:
        if "Total Revenue" in financials.index and "Net Income" in financials.index:
            rev = financials.loc["Total Revenue"].dropna()
            ni = financials.loc["Net Income"].dropna()
            if len(rev) >= 1 and len(ni) >= 1 and rev.iloc[0] != 0:
                result["profit_margin"] = float((ni.iloc[0] / rev.iloc[0]) * 100)
    except Exception:
        pass

    try:
        if balance_sheet is not None and "Total Debt" in balance_sheet.index and "Stockholders Equity" in balance_sheet.index:
            debt = balance_sheet.loc["Total Debt"].dropna()
            equity = balance_sheet.loc["Stockholders Equity"].dropna()
            if len(debt) >= 1 and len(equity) >= 1 and equity.iloc[0] != 0:
                result["debt_equity"] = float(debt.iloc[0] / equity.iloc[0])
    except Exception:
        pass

    try:
        if "Net Income" in financials.index and balance_sheet is not None and "Stockholders Equity" in balance_sheet.index:
            ni = financials.loc["Net Income"].dropna()
            equity = balance_sheet.loc["Stockholders Equity"].dropna()
            if len(ni) >= 1 and len(equity) >= 1 and equity.iloc[0] != 0:
                result["roe"] = float((ni.iloc[0] / equity.iloc[0]) * 100)
    except Exception:
        pass

    try:
        if cashflow is not None and "Free Cash Flow" in cashflow.index:
            fcf = cashflow.loc["Free Cash Flow"].dropna()
            if len(fcf) >= 1:
                result["fcf"] = float(fcf.iloc[0])
    except Exception:
        pass

    try:
        if "Diluted EPS" in financials.index:
            eps = financials.loc["Diluted EPS"].dropna()
            if len(eps) >= 2:
                result["eps_growth"] = float((eps.iloc[0] / eps.iloc[1] - 1) * 100)
    except Exception:
        pass

    return result


def _fetch_price_fallback(ticker: str) -> Optional[Dict[str, Any]]:
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        resp = _REQUEST_SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        if price:
            return {"price": price, "sector": None, "_fallback": True}
    except Exception:
        pass
    return None


def _fetch_yahoo_fundamentals(ticker: str) -> Optional[Dict[str, Any]]:
    """Fetch fundamental data from Yahoo quoteSummary API (same domain as chart, may work on cloud)."""
    try:
        url = (
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=financialData,defaultKeyStatistics,summaryDetail,price"
        )
        resp = _REQUEST_SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("quoteSummary", {}).get("result", [{}])[0]
        if not result:
            return None

        fin = result.get("financialData", {}) or {}
        stat = result.get("defaultKeyStatistics", {}) or {}
        detail = result.get("summaryDetail", {}) or {}

        fields: Dict[str, Any] = {}

        raw = fin.get("revenueGrowth", {}).get("raw")
        if raw is not None:
            fields["revenue_growth"] = raw * 100
        raw = fin.get("earningsGrowth", {}).get("raw")
        if raw is not None:
            fields["eps_growth"] = raw * 100
        raw = fin.get("profitMargins", {}).get("raw")
        if raw is not None:
            fields["profit_margin"] = raw * 100
        raw = fin.get("returnOnEquity", {}).get("raw")
        if raw is not None:
            fields["roe"] = raw * 100
        raw = fin.get("debtToEquity", {}).get("raw")
        if raw is not None:
            # Yahoo reports debtToEquity in percentage points (150 means 1.5x).
            fields["debt_equity"] = raw / 100.0
        raw = fin.get("pegRatio", {}).get("raw")
        if raw is not None:
            fields["peg"] = raw
        raw = stat.get("beta", {}).get("raw")
        if raw is not None:
            fields["beta"] = raw
        raw = detail.get("dividendYield", {}).get("raw")
        if raw is not None:
            fields["dividend_yield"] = raw
        raw = fin.get("targetMeanPrice", {}).get("raw")
        if raw is not None:
            fields["target_mean_price"] = raw
        raw = detail.get("trailingPE", {}).get("raw")
        if raw is not None:
            fields["pe_ratio"] = raw
        raw = detail.get("forwardPE", {}).get("raw")
        if raw is not None:
            fields["forward_pe"] = raw
        raw = detail.get("marketCap", {}).get("raw")
        if raw is not None:
            fields["market_cap"] = raw
        raw = stat.get("shortRatio", {}).get("raw")
        if raw is not None:
            fields["short_ratio"] = raw
        raw = fin.get("recommendationMean", {}).get("raw")
        if raw is not None:
            fields["recommendation_mean"] = raw
            rating_map = {1: "Strong Buy", 2: "Buy", 3: "Hold", 4: "Underperform", 5: "Sell"}
            fields["rating_label"] = rating_map.get(round(raw), "N/A")
        raw = fin.get("numberOfAnalystOpinions", {}).get("raw")
        if raw is not None:
            fields["number_of_analysts"] = raw
        raw = stat.get("heldPercentInstitutions", {}).get("raw")
        if raw is not None:
            fields["held_percent_institutions"] = raw
        raw = detail.get("fiftyTwoWeekHigh", {}).get("raw")
        if raw is not None:
            fields["fifty_two_week_high"] = raw
        raw = detail.get("fiftyTwoWeekLow", {}).get("raw")
        if raw is not None:
            fields["fifty_two_week_low"] = raw
        raw = fin.get("targetHighPrice", {}).get("raw")
        if raw is not None:
            fields["target_high_price"] = raw
        raw = fin.get("targetLowPrice", {}).get("raw")
        if raw is not None:
            fields["target_low_price"] = raw
        raw = stat.get("enterpriseToEbitda", {}).get("raw")
        if raw is not None:
            fields["ev_ebitda"] = raw
        lname = result.get("price", {}).get("longName")
        if lname and isinstance(lname, str):
            fields["longName"] = lname

        if fields:
            fields["_source"] = "yahoo_fundamentals"
            return fields
    except Exception:
        pass
    return None


def fetch_all_stocks(
    selected_tickers: Optional[list[str]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    force_refresh: bool = False,
) -> Dict[str, Dict[str, Any]]:
    tickers = selected_tickers or [s["ticker"] for s in STOCK_UNIVERSE]
    results: Dict[str, Dict[str, Any]] = {}
    completed = 0
    total = len(tickers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {executor.submit(fetch_stock_data, t, force_refresh): t for t in tickers}
        for future in concurrent.futures.as_completed(future_map):
            ticker = future_map[future]
            try:
                results[ticker] = future.result()
            except Exception as e:
                results[ticker] = {"ticker": ticker, "error": str(e)}
            completed += 1
            if progress_callback:
                progress_callback(completed, total)

    return results


_POSITIVE_KEYWORDS: set[str] = {
    "beat", "surge", "surges", "surged", "growth", "upgrade", "upgraded",
    "launch", "launched", "record", "positive", "partnership", "strong",
    "rally", "gain", "gains", "bull", "bullish", "raised", "exceed",
    "outperform", "innovation", "milestone", "soar", "soars", "boom",
    "expands", "expansion", "profit", "profitable", "momentum",
}

_NEGATIVE_KEYWORDS: set[str] = {
    "drop", "drops", "dropped", "miss", "missed", "downgrade", "downgraded",
    "lawsuit", "investigation", "layoff", "layoffs", "recall", "loss",
    "decline", "declines", "fall", "falls", "fell", "cut", "sell",
    "bear", "bearish", "warning", "risk", "regulatory", "fine", "fined",
    "probe", "scrutiny", "slowdown", "weak", "weakness", "struggle",
    "volatile", "uncertainty", "penalty",
}


def _analyze_news_sentiment(title: str, summary: str) -> str:
    text = (title + " " + summary).strip()
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        analyzer = getattr(_analyze_news_sentiment, "_analyzer", None)
        if analyzer is None:
            analyzer = SentimentIntensityAnalyzer()
            setattr(_analyze_news_sentiment, "_analyzer", analyzer)
        compound = analyzer.polarity_scores(text)["compound"]
        if compound >= 0.05:
            return "positive"
        if compound <= -0.05:
            return "negative"
        return "neutral"
    except (ImportError, OSError):
        pass
    text = text.lower()
    pos_count = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text)
    neg_count = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text)
    if pos_count > neg_count:
        return "positive"
    elif neg_count > pos_count:
        return "negative"
    return "neutral"


def _extract_news(item: dict) -> dict:
    content = item.get("content") or item
    title = content.get("title", "")
    summary = (content.get("summary", "") or content.get("description", ""))
    publisher = content.get("publisher", "") or item.get("publisher", "")
    provider = content.get("provider")
    if provider:
        publisher = provider.get("displayName", "") or provider.get("name", "")
    link = ""
    canonical = content.get("canonicalUrl")
    if canonical:
        link = canonical.get("url", "")
    if not link:
        click = content.get("clickThroughUrl")
        if click:
            link = click.get("url", "")
    link = link or content.get("link", "") or item.get("link", "")
    published = content.get("pubDate") or content.get("displayTime") or item.get("providerPublishTime")
    published_at = _normalize_news_time(published)
    article_id = str(content.get("id") or item.get("uuid") or "")
    if not article_id:
        article_id = hashlib.sha256(f"{link}|{title}".encode("utf-8")).hexdigest()[:20]
    return {
        "id": article_id,
        "title": title,
        "summary": summary,
        "publisher": publisher,
        "link": link,
        "published_at": published_at,
        "content_type": content.get("contentType") or item.get("type"),
    }


def fetch_news(
    ticker: str, max_items: int = 5, force_refresh: bool = False,
    max_age_days: int = NEWS_MAX_AGE_DAYS, now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    cache_key = f"news_v3_{ticker}_{max_age_days}d"
    cached = None if force_refresh else cache.get(cache_key, "info", ttl=900)
    if cached is not None:
        return cached[:max_items]

    try:
        stock = yf.Ticker(ticker, session=_REQUEST_SESSION)
        raw_news = stock.news
        if not raw_news:
            cache.set(cache_key, "info", [], ttl=300)
            return []
    except Exception as e:
        agent_state.log_source_result(f"news:{ticker}", False, str(e))
        return []

    result: list[dict[str, Any]] = []
    seen = set()
    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=max_age_days)
    for item in raw_news[:20]:
        parsed = _extract_news(item)
        title = parsed["title"]
        published = pd.Timestamp(parsed["published_at"]) if parsed.get("published_at") else None
        if (
            not title or parsed["id"] in seen
            or published is None or published < pd.Timestamp(cutoff)
            or published > pd.Timestamp(current_time + timedelta(minutes=10))
            or not _news_is_relevant(ticker, title, parsed["summary"])
        ):
            continue
        seen.add(parsed["id"])
        summary = parsed["summary"]
        sentiment = _analyze_news_sentiment(title, summary)
        result.append({
            "ticker": ticker,
            "id": parsed["id"],
            "title": title,
            "summary": summary,
            "publisher": parsed["publisher"],
            "link": parsed["link"],
            "sentiment": sentiment,
            "published_at": parsed["published_at"],
            "content_type": parsed["content_type"],
            "source": "yahoo",
            "fetched_at": current_time.isoformat(),
            "cutoff_at": cutoff.isoformat(),
        })

    result.sort(key=lambda article: article.get("published_at") or "", reverse=True)
    agent_state.log_source_result(f"news:{ticker}", True)
    cache.set(cache_key, "info", result, ttl=900)
    return result[:max_items]


def _news_is_relevant(ticker: str, title: str, summary: str) -> bool:
    original_text = f"{title} {summary}"
    text = original_text.lower()
    ticker = ticker.upper()
    if len(ticker) <= 3:
        structured = (
            re.search(rf"\${re.escape(ticker)}\b", original_text)
            or re.search(rf"\({re.escape(ticker)}\)", original_text)
            or re.search(rf"\b(?:NYSE|NASDAQ):{re.escape(ticker)}\b", original_text)
        )
        if structured:
            return True
        aliases = set(alias.lower() for alias in NEWS_DISTINCTIVE_ALIASES.get(ticker, []))
    else:
        aliases = {ticker.lower()}
    metadata = next((stock for stock in STOCK_UNIVERSE if stock["ticker"] == ticker), {})
    english_name = str(metadata.get("name_en") or "").lower()
    if english_name:
        aliases.add(english_name)
        core_name = re.sub(r"\b(inc|corp|corporation|company|holdings|ltd|plc)\.?\b", "", english_name).strip()
        if len(core_name) >= 3:
            aliases.add(core_name)
    return any(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) for alias in aliases if alias)


def fetch_next_earnings(ticker: str, force_refresh: bool = False) -> Dict[str, Any]:
    cache_key = f"earnings_calendar_v2_{ticker}"
    cached = None if force_refresh else cache.get(cache_key, "info", ttl=21600)
    if cached is not None:
        return cached
    stock = yf.Ticker(ticker, session=_REQUEST_SESSION)
    result: Dict[str, Any] = {"available": False, "source": None}
    today_et = datetime.now(EASTERN).date()
    try:
        calendar = stock.calendar
        dates = _calendar_earnings_dates(calendar)
        future = [value for value in dates if value >= today_et]
        if future:
            result = _earnings_result(future, "yahoo_calendar", today=today_et)
    except Exception as exc:
        result["calendar_error"] = str(exc)
    if not result.get("available"):
        try:
            dates_frame = stock.get_earnings_dates(limit=12)
            if dates_frame is not None and not dates_frame.empty:
                dates = [_earnings_date_et(timestamp) for timestamp in dates_frame.index]
                future = [value for value in dates if value >= today_et]
                if future:
                    result = _earnings_result(future, "yahoo_earnings_dates", today=today_et)
        except Exception as exc:
            result["fallback_error"] = str(exc)
    cache.set(cache_key, "info", result, ttl=21600)
    return result


def _normalize_news_time(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        parsed = pd.to_datetime(value, utc=True)
        return parsed.isoformat() if not pd.isna(parsed) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _calendar_earnings_dates(calendar: Any) -> list[date]:
    values: Any = None
    if isinstance(calendar, dict):
        values = calendar.get("Earnings Date")
    elif isinstance(calendar, pd.DataFrame) and "Earnings Date" in calendar.index:
        values = calendar.loc["Earnings Date"]
    if values is None:
        return []
    if not isinstance(values, (list, tuple, pd.Series, pd.Index)):
        values = [values]
    result = []
    for value in values:
        try:
            result.append(_earnings_date_et(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(result))


def _earnings_date_et(value: Any) -> date:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.date()
    return timestamp.tz_convert("America/New_York").date()


def _earnings_result(dates: list[date], source: str, today: Optional[date] = None) -> Dict[str, Any]:
    start, end = min(dates), max(dates)
    current_date = today or datetime.now(EASTERN).date()
    return {
        "available": True,
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "next_date": start.isoformat(),
        "days_until": (start - current_date).days,
        "precision": "exact" if start == end else "range",
        "source": source,
    }


def fetch_financials_history(ticker: str) -> dict[str, list[float]]:
    cached = cache.get(f"hist_{ticker}", "financials")
    if cached:
        return cached

    try:
        stock = yf.Ticker(ticker, session=_REQUEST_SESSION)
        fin = stock.financials
        if fin is None or fin.empty:
            return {}
    except Exception:
        return {}

    result: dict[str, list[float]] = {}
    try:
        if "Total Revenue" in fin.index:
            rev = fin.loc["Total Revenue"].dropna().tolist()
            result["revenue"] = [float(v) for v in rev[:5]]
    except Exception:
        pass
    try:
        if "Net Income" in fin.index:
            ni = fin.loc["Net Income"].dropna().tolist()
            result["net_income"] = [float(v) for v in ni[:5]]
    except Exception:
        pass
    try:
        if "Diluted EPS" in fin.index:
            eps = fin.loc["Diluted EPS"].dropna().tolist()
            result["eps"] = [float(v) for v in eps[:5]]
    except Exception:
        pass

    if result:
        cache.set(f"hist_{ticker}", "financials", result)
    return result


def fetch_options_chain(
    ticker: str, current_price: Optional[float] = None, force_refresh: bool = False,
) -> Dict[str, Any]:
    cooldown_key = f"options_provider_cooldown_v2_{ticker}"
    cooldown = cache.get(cooldown_key, "info", ttl=300)
    if cooldown is not None:
        return {**cooldown, "from_cache": True}
    spot_key = f"{round(float(current_price), 1):.1f}" if current_price else "auto"
    cache_key = f"options_v2_{ticker}_{spot_key}"
    cached = None if force_refresh else cache.get(cache_key, "info", ttl=60)
    if cached is not None:
        result = dict(cached)
        result["from_cache"] = True
        return result
    try:
        stock = yf.Ticker(ticker)
        expirations = stock.options
        if not expirations:
            return _options_fallback(ticker, current_price, cache_key, cooldown_key, "empty_expirations")
        nearest = expirations[0]
        selected_expiry = _select_option_expiry(expirations)
        chain = stock.option_chain(selected_expiry)
        calls = chain.calls
        puts = chain.puts
        if calls.empty and puts.empty:
            return _options_fallback(ticker, current_price, cache_key, cooldown_key, "empty_chain")
        atm_strike = None
        spot = current_price or get_latest_price(stock)[0]
        if spot:
            if not calls.empty:
                idx = (calls["strike"] - spot).abs().idxmin()
                atm_strike = float(calls.loc[idx, "strike"])
            elif not puts.empty:
                idx = (puts["strike"] - spot).abs().idxmin()
                atm_strike = float(puts.loc[idx, "strike"])
        result = {
            "ticker": ticker,
            "expirations": list(expirations),
            "nearest_expiry": nearest,
            "selected_expiry": selected_expiry,
            "num_calls": len(calls),
            "num_puts": len(puts),
            "atm_strike": atm_strike,
            "max_call_oi": float(calls.loc[calls["openInterest"].idxmax(), "strike"]) if not calls.empty and calls["openInterest"].max() > 0 else None,
            "max_put_oi": float(puts.loc[puts["openInterest"].idxmax(), "strike"]) if not puts.empty and puts["openInterest"].max() > 0 else None,
            "max_call_volume": float(calls.loc[calls["volume"].idxmax(), "strike"]) if not calls.empty and calls["volume"].max() > 0 else None,
            "max_put_volume": float(puts.loc[puts["volume"].idxmax(), "strike"]) if not puts.empty and puts["volume"].max() > 0 else None,
            "put_call_ratio": len(puts) / len(calls) if len(calls) > 0 else None,
            "calls": _option_contracts(calls, spot),
            "puts": _option_contracts(puts, spot),
            "source": "yfinance_options",
            "fetched_at": _now_str(),
            "as_of": _now_str(),
            "from_cache": False,
        }
        cache.set(cache_key, "info", result, ttl=60)
        return result
    except Exception as exc:
        message = str(exc)
        reason = "yahoo_rate_limit" if _is_rate_limit_error(message) else "yahoo_request_failed"
        return _options_fallback(ticker, current_price, cache_key, cooldown_key, reason)


def fetch_trading_session_ranges(ticker: str, force_refresh: bool = False) -> Dict[str, Any]:
    cache_key = f"session_ranges_v2_{ticker}"
    cached = None if force_refresh else cache.get(cache_key, "info", ttl=60)
    if cached is not None:
        result = dict(cached)
        result["from_cache"] = True
        return result
    try:
        result = get_session_ranges(yf.Ticker(ticker))
        result["fetched_at"] = _now_str()
        result["as_of"] = max(
            (session.get("end_time") for session in result.get("sessions", {}).values() if session.get("end_time")),
            default=None,
        )
        result["from_cache"] = False
        cache.set(cache_key, "info", result, ttl=60)
        return result
    except Exception as exc:
        return {"error": str(exc), "sessions": {}}


def _option_contracts(chain: Any, spot: Optional[float], limit: int = 12) -> list[Dict[str, Any]]:
    if chain is None or chain.empty:
        return []
    contracts = chain.copy()
    if spot:
        contracts["_distance"] = (contracts["strike"] - spot).abs()
        contracts = contracts.sort_values(["_distance", "openInterest", "volume"], ascending=[True, False, False])
    rows = []
    for _, contract in contracts.head(limit).iterrows():
        bid = _finite_float(contract.get("bid"))
        ask = _finite_float(contract.get("ask"))
        last = _finite_float(contract.get("lastPrice"))
        mid = round((bid + ask) / 2, 2) if bid is not None and ask is not None and ask >= bid else last
        spread_pct = None
        if bid is not None and ask is not None and mid and ask >= bid:
            spread_pct = round((ask - bid) / mid * 100, 1)
        rows.append({
            "contract_symbol": contract.get("contractSymbol"),
            "strike": _finite_float(contract.get("strike")),
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "last": last,
            "volume": int(contract.get("volume") or 0),
            "open_interest": int(contract.get("openInterest") or 0),
            "implied_volatility": _finite_float(contract.get("impliedVolatility")),
            "in_the_money": bool(contract.get("inTheMoney", False)),
            "spread_pct": spread_pct,
            "last_trade_time": (
                contract.get("lastTradeDate").isoformat()
                if hasattr(contract.get("lastTradeDate"), "isoformat") else None
            ),
        })
    return rows


def _select_option_expiry(expirations: Any, minimum_days: int = 21) -> str:
    today = datetime.now(EASTERN).date()
    for expiry in expirations:
        try:
            if (datetime.strptime(expiry, "%Y-%m-%d").date() - today).days >= minimum_days:
                return expiry
        except (TypeError, ValueError):
            continue
    return expirations[-1]


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if number == number and number not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _is_rate_limit_error(message: str) -> bool:
    normalized = message.casefold()
    return "too many requests" in normalized or "rate limit" in normalized or "429" in normalized


def _options_provider_error(
    reason: str, error_code: str = "provider_incomplete", source: str = "yfinance_options",
) -> Dict[str, Any]:
    return {
        "error": "rate_limited" if error_code == "rate_limit" else "provider_unavailable",
        "error_code": error_code,
        "provider_reason": reason,
        "retry_after_seconds": 300,
        "source": source,
        "fetched_at": _now_str(),
        "from_cache": False,
    }


def _options_fallback(
    ticker: str, current_price: Optional[float], cache_key: str, cooldown_key: str,
    yahoo_reason: str,
) -> Dict[str, Any]:
    result = fetch_cboe_options_chain(ticker, current_price)
    if not result.get("error"):
        result["fallback_from"] = "yfinance_options"
        result["fallback_reason"] = yahoo_reason
        cache.set(cache_key, "info", result, ttl=60)
        return result
    cboe_code = result.get("error_code", "provider_error")
    if "rate_limit" in yahoo_reason or cboe_code == "rate_limit":
        error_code = "rate_limit"
    elif yahoo_reason in {"empty_expirations", "empty_chain"} and cboe_code == "provider_incomplete":
        error_code = "provider_incomplete"
    else:
        error_code = "provider_error"
    combined = _options_provider_error(
        f"{yahoo_reason};cboe_{result.get('provider_reason', 'failed')}",
        error_code=error_code,
        source="multi_source_options",
    )
    combined["fallback_sources"] = ["yfinance_options", "cboe_delayed_options"]
    cache.set(cooldown_key, "info", combined, ttl=300)
    return combined


def suggest_options_fallback(ticker: str, technical_data: Dict[str, Any], current_price: float) -> Dict[str, Any]:
    opt = fetch_options_chain(ticker)
    if "error" in opt:
        return {"error": opt["error"], "option_type": "none"}
    rsi = technical_data.get("rsi_14")
    macd_hist = technical_data.get("macd_histogram")
    bb_signal = technical_data.get("bb_signal")
    trend = technical_data.get("trend_signal")
    support = technical_data.get("bb_lower") or current_price * 0.9
    resistance = technical_data.get("bb_upper") or current_price * 1.1
    option_type = "none"
    if trend == "uptrend" and rsi and rsi < 65:
        option_type = "call"
    elif trend == "downtrend" and rsi and rsi > 35:
        option_type = "put"
    elif rsi and rsi >= 70:
        option_type = "put"
    elif rsi and rsi <= 30:
        option_type = "call"
    strike = opt.get("atm_strike")
    if strike is None:
        strike = round(current_price, 0)
    expiry = opt.get("nearest_expiry", "next monthly")
    return {
        "option_type": option_type,
        "strike_price": strike,
        "expiration": expiry,
        "key_support": round(support, 2) if support else None,
        "key_resistance": round(resistance, 2) if resistance else None,
        "reasoning": "",
    }


INDUSTRY_NEWS_SOURCES: Dict[str, str] = {
    "Semiconductors": "SMH",
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial": "XLF",
    "Consumer": "XLY",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Space": "ARKX",
    "Memory & Storage": "SMH",
    "Defense & Aerospace": "ITA",
}

_CLIMATE_KEYWORDS = [
    "climate", "warming", "carbon", "emission", "renewable", "solar", "wind",
    "green energy", "clean energy", "net zero", "global warming", "weather",
    "heat wave", "flood", "drought", "wildfire", "hurricane", "COP",
    "environmental", "ESG", "sustainable", "fossil fuel", "pollution",
]


def _is_climate_related(title: str, summary: str = "") -> bool:
    text = (title + " " + summary).lower()
    return any(kw in text for kw in _CLIMATE_KEYWORDS)


def fetch_industry_news(
    max_per_source: int = 3,
    max_total: int = 20,
) -> Dict[str, Any]:
    cached = cache.get("industry_news", "info")
    if cached:
        return cached

    result: Dict[str, Any] = {
        "by_sector": {},
        "climate_items": [],
        "all_headlines": [],
    }

    for sector_name, etf_ticker in INDUSTRY_NEWS_SOURCES.items():
        try:
            stock = yf.Ticker(etf_ticker, session=_REQUEST_SESSION)
            raw = stock.news
            if not raw:
                continue
            sector_news: list[Dict[str, Any]] = []
            for item in raw[:max_per_source]:
                parsed = _extract_news(item)
                title = parsed["title"]
                summary = parsed["summary"]
                entry = {
                    "title": title,
                    "summary": summary[:300],
                    "publisher": parsed["publisher"],
                    "link": parsed["link"],
                    "sector": sector_name,
                }
                sector_news.append(entry)
                result["all_headlines"].append(dict(entry))
                if _is_climate_related(title, summary):
                    result["climate_items"].append(dict(entry))
            if sector_news:
                result["by_sector"][sector_name] = sector_news
        except Exception:
            pass

    result["all_headlines"] = result["all_headlines"][:max_total]
    agent_state.log_source_result("industry_news", bool(result["all_headlines"]),
                                  "" if result["all_headlines"] else "no news")
    cache.set("industry_news", "info", result)
    return result


def suggest_price_fallback(ticker: str, technical_data: Dict[str, Any], current_price: float) -> Dict[str, Any]:
    bb_upper = technical_data.get("bb_upper")
    bb_lower = technical_data.get("bb_lower")
    sma20 = technical_data.get("sma_20")
    sma50 = technical_data.get("sma_50")
    rsi = technical_data.get("rsi_14")
    buy = None
    sell = None
    stop = None
    confidence = 50
    if rsi is not None:
        if rsi >= 70:
            confidence = 65
            sell = round(current_price * 1.05, 2) if bb_upper else round(current_price * 1.03, 2)
            stop = round(current_price * 0.95, 2)
        elif rsi <= 30:
            confidence = 65
            buy = round(current_price, 2)
            sell = round(current_price * 1.10, 2) if bb_upper else round(current_price * 1.05, 2)
            stop = round(current_price * 0.93, 2)
        else:
            confidence = 50
            buy = round(current_price * 0.97, 2) if sma20 else None
            sell = round(bb_upper, 2) if bb_upper else round(current_price * 1.05, 2)
            stop = round(sma50 or current_price * 0.92, 2)
    return {
        "buy_price": buy,
        "sell_price": sell,
        "stop_loss": stop,
        "confidence": confidence,
        "reasoning": "",
    }
