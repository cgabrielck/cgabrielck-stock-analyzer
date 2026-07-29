"""
Backtesting engine for the stock analyzer scoring system.

Validates whether the scoring system's recommendations would have
outperformed the market (SPY) historically.

Methodology:
  - Monthly rebalancing at each month-end
  - For each rebalance date:
      1. Technical indicators from price data up to that date (no look-ahead)
      2. Fundamental data from the most recent quarterly report with filing lag
      3. Combined scoring matching the main system
      4. Top 5 stocks selected with sector diversification (max 2 per sector)
  - Track forward 1-month performance vs SPY
"""

import math
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from agents.technical_analyzer import (
    _compute_rsi,
    _compute_macd,
    _compute_sma,
    _compute_bb,
    _compute_atr,
    _compute_adx,
)
from agents.signal_pillars import calculate_five_pillar_score
from agents.portfolio_manager import (
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_UPISDE_PCT,
    kelly_fraction,
    normalize_capped_weights,
)
from agents.market_regime import classify_market_regime
from agents.risk_analyzer import calculate_risk_adjusted_score, calculate_risk_metrics, risk_label
from backtesting.calibration import ExpandingScoreCalibrator, save_calibration_snapshot
from backtesting.statistics import (
    DEFAULT_BOOTSTRAP_ITERATIONS,
    DEFAULT_BOOTSTRAP_SEED,
    STATISTICS_VERSION,
    bootstrap_ci,
    effective_sample_size,
)
from backtesting.universe import HistoricalUniverse
from agents.alpha_vantage_data import fetch_daily_adjusted
from utils.cache import cache
from utils.constants import STOCK_UNIVERSE, SCORING_WEIGHTS
from utils.selection import MIN_RECOMMENDATION_METRICS, select_recommendations

FILING_LAG_DAYS: int = 60
MAX_WORKERS: int = 5
DEFAULT_START: str = (datetime.now() - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
TECH_CANDIDATES: int = 15
ENTRY_THRESHOLD: float = 60.0
FILL_THRESHOLD: float = 50.0
MAX_PER_SECTOR: int = 2
TOP_N: int = 5

ProgressCb = Optional[Callable[[int, int, str], None]]


# ---------------------------------------------------------------------------
# Historical data fetching
# ---------------------------------------------------------------------------

def _fetch_single_price(ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(start=start, end=end, auto_adjust=True)
        if df is not None and not df.empty:
            return df
    except Exception:
        pass
    alpha_history = fetch_daily_adjusted(ticker, start=start, end=end)
    if alpha_history:
        return alpha_history["data"]
    return None


def fetch_price_data(
    tickers: List[str],
    start: str = DEFAULT_START,
    end: Optional[str] = None,
) -> Dict[str, pd.DataFrame]:
    end = end or datetime.now().strftime("%Y-%m-%d")
    results: Dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_single_price, t, start, end): t for t in tickers}
        for future in as_completed(futures):
            t = futures[future]
            try:
                df = future.result()
                if df is not None and not df.empty:
                    results[t] = df
            except Exception:
                pass
    return results


def _fetch_single_fundamentals(ticker: str) -> Optional[pd.DataFrame]:
    cached = cache.get(f"bt_fund_{ticker}", "financials")
    if cached is not None:
        return pd.DataFrame(cached) if isinstance(cached, dict) else cached
    try:
        stock = yf.Ticker(ticker)
        qf = stock.quarterly_financials
        if qf is not None and not qf.empty:
            cache.set(f"bt_fund_{ticker}", "financials", qf.to_dict())
            return qf
    except Exception:
        pass
    return None


def _fetch_single_balance_sheet(ticker: str) -> Optional[pd.DataFrame]:
    cached = cache.get(f"bt_bs_{ticker}", "financials")
    if cached is not None:
        return pd.DataFrame(cached) if isinstance(cached, dict) else cached
    try:
        stock = yf.Ticker(ticker)
        bs = stock.quarterly_balance_sheet
        if bs is not None and not bs.empty:
            cache.set(f"bt_bs_{ticker}", "financials", bs.to_dict())
            return bs
    except Exception:
        pass
    return None


def _find_recent_quarter(qf: pd.DataFrame, as_of: pd.Timestamp) -> Tuple[int, int]:
    cutoff = as_of - timedelta(days=FILING_LAG_DAYS)
    available = [c for c in qf.columns if pd.Timestamp(c) <= cutoff]
    if not available:
        return -1, -1
    col = min(available, key=lambda c: abs((pd.Timestamp(c) - cutoff).days))
    idx = list(qf.columns).index(col)
    # Quarterly growth must compare the same fiscal quarter one year earlier.
    prev = idx + 4
    if prev >= len(qf.columns):
        return -1, -1
    return idx, prev


def _extract_fundamentals_as_of(
    qf: pd.DataFrame,
    bs: Optional[pd.DataFrame],
    price_df: Optional[pd.DataFrame],
    as_of: pd.Timestamp,
) -> Dict[str, Any]:
    if qf is None or qf.empty:
        return {}

    filing_as_of = as_of.tz_localize(None) if as_of.tzinfo is not None else as_of
    idx, prev = _find_recent_quarter(qf, filing_as_of)
    if idx < 0 or prev < 0:
        # Never substitute a future report for an unavailable historical one.
        return {}
    result: Dict[str, Any] = {}

    try:
        rev_current = None
        rev_prev = None
        if "Total Revenue" in qf.index:
            rev_current = qf.loc["Total Revenue"].iloc[idx]
            rev_prev = qf.loc["Total Revenue"].iloc[prev]
            if pd.notna(rev_current) and pd.notna(rev_prev) and rev_prev != 0:
                result["revenue_growth"] = float((rev_current / rev_prev - 1) * 100)

        ni = None
        if "Net Income" in qf.index:
            ni = qf.loc["Net Income"].iloc[idx]
            if pd.notna(ni):
                result["net_income"] = float(ni)

        if rev_current is not None and ni is not None and pd.notna(rev_current) and pd.notna(ni) and rev_current != 0:
            result["profit_margin"] = float((ni / rev_current) * 100)

        eps_current = None
        eps_prev = None
        if "Diluted EPS" in qf.index:
            eps_current = qf.loc["Diluted EPS"].iloc[idx]
            eps_prev = qf.loc["Diluted EPS"].iloc[prev]
            if pd.notna(eps_current) and pd.notna(eps_prev) and eps_prev != 0:
                result["eps_growth"] = float((eps_current / eps_prev - 1) * 100)

        if bs is not None and not bs.empty:
            cutoff = filing_as_of - timedelta(days=FILING_LAG_DAYS)
            bs_available = [c for c in bs.columns if pd.Timestamp(c).tz_localize(None) <= cutoff]
            if not bs_available:
                return result
            bs_col = min(bs_available, key=lambda c: abs((pd.Timestamp(c).tz_localize(None) - cutoff).days))
            bs_idx = list(bs.columns).index(bs_col)
            equity = None
            if "Stockholders Equity" in bs.index:
                eq_val = bs.loc["Stockholders Equity"].iloc[bs_idx]
                if pd.notna(eq_val):
                    equity = float(eq_val)
                    if ni is not None and pd.notna(ni) and equity != 0:
                        result["roe"] = float((ni / equity) * 100)

            total_debt = None
            if "Total Debt" in bs.index:
                td_val = bs.loc["Total Debt"].iloc[bs_idx]
                if pd.notna(td_val):
                    total_debt = float(td_val)

            if "Long Term Debt" in bs.index and total_debt is None:
                ltd = bs.loc["Long Term Debt"].iloc[bs_idx]
                if pd.notna(ltd):
                    total_debt = float(ltd)

            if total_debt is not None and equity is not None and equity != 0:
                result["debt_equity"] = round(total_debt / equity, 4)

        if price_df is not None and not price_df.empty:
            price_as_of_timestamp = as_of
            if price_df.index.tz is not None and as_of.tzinfo is None:
                price_as_of_timestamp = as_of.tz_localize(price_df.index.tz)
            elif price_df.index.tz is None and as_of.tzinfo is not None:
                price_as_of_timestamp = as_of.tz_localize(None)
            price_as_of = price_df[price_df.index <= price_as_of_timestamp]
            if price_as_of.empty:
                return result
            current_price = float(price_as_of["Close"].iloc[-1])
            if current_price > 0 and eps_current is not None and pd.notna(eps_current) and eps_current != 0:
                pe = current_price / abs(float(eps_current)) if eps_current != 0 else None
                eps_g = result.get("eps_growth", 0)
                if pe is not None and eps_g and eps_g > 0:
                    # Conventional PEG divides P/E by percentage growth (20, not 0.20).
                    result["peg"] = round(pe / eps_g, 2)

    except (IndexError, KeyError):
        pass

    return result


# ---------------------------------------------------------------------------
# Technical scoring on historical data
# ---------------------------------------------------------------------------

def _score_stock_technical(
    hist: pd.DataFrame, ticker: str, pillar_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    if len(hist) < 50:
        return {"ticker": ticker, "score": 0, "error": "insufficient_history"}

    close = hist["Close"]
    volume = hist["Volume"]
    high = hist["High"]
    low = hist["Low"]

    current_price = float(close.iloc[-1])

    rsi = _compute_rsi(close, 14)
    macd_line, macd_signal, macd_hist = _compute_macd(close)
    bb_upper, bb_lower = _compute_bb(close)
    sma20 = _compute_sma(close, 20)
    sma50 = _compute_sma(close, 50)
    atr = _compute_atr(high, low, close)

    price_vs_sma50 = ((current_price / sma50) - 1) * 100 if sma50 and sma50 > 0 else None

    vol_short = float(volume.tail(10).mean())
    vol_long = float(volume.tail(50).mean())
    volume_ratio = vol_short / vol_long if vol_long > 0 else None
    dollar_volume = close * volume
    dollar_volume_10d_avg = float(dollar_volume.tail(10).mean())
    dollar_volume_50d_avg = float(dollar_volume.tail(50).mean())
    dollar_volume_ratio = dollar_volume_10d_avg / dollar_volume_50d_avg if dollar_volume_50d_avg > 0 else None
    volume_above_avg = volume > volume.rolling(20).mean()
    signed_quality = close.diff().apply(lambda value: 1 if value > 0 else -1 if value < 0 else 0)
    quality_signal = signed_quality.where(volume_above_avg, 0).tail(20)
    obv = (np.sign(close.diff()).fillna(0) * volume).cumsum()
    technical = {
        "ticker": ticker,
        "price": current_price,
        "rsi_14": rsi,
        "macd_histogram": macd_hist,
        "sma_20": sma20,
        "sma_50": sma50,
        "atr_14": atr,
        "volume_ratio_10_50": volume_ratio,
        "dollar_volume_10d_avg": dollar_volume_10d_avg,
        "dollar_volume_50d_avg": dollar_volume_50d_avg,
        "dollar_volume_ratio": dollar_volume_ratio,
        "volume_quality_score": float(max(0, min(100, quality_signal.mean() * 50 + 50))),
        "obv_trend": float(obv.tail(10).mean() - obv.tail(30).mean()) if len(obv) >= 30 else None,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "sma_200": _compute_sma(close, 200),
        "adx_14": _compute_adx(high, low, close, 14),
        "trend_signal": "uptrend" if sma50 and current_price > sma50 else "downtrend",
    }
    risk_metrics = calculate_risk_metrics(close)
    pillar_result = calculate_five_pillar_score(technical, risk_metrics, pillar_weights)
    technical["signal_pillars"] = pillar_result
    technical["total_score"] = pillar_result["score"] or 0
    return technical


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def _index_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    return pd.Timestamp(value).date().isoformat()


def _close_on_date(frame: pd.DataFrame, target_date: Optional[str]) -> Optional[float]:
    if frame is None or frame.empty or not target_date or "Close" not in frame:
        return None
    dates = pd.DatetimeIndex(frame.index).tz_localize(None).normalize()
    matches = frame.loc[dates == pd.Timestamp(target_date), "Close"]
    if matches.empty:
        return None
    value = pd.to_numeric(matches.iloc[-1], errors="coerce")
    return float(value) if np.isfinite(value) and value > 0 else None


class BacktestResult:
    def __init__(self) -> None:
        self.periods: List[Dict[str, Any]] = []
        self.tickers_picked: Dict[str, int] = {}
        self.sector_counts: Dict[str, int] = {}
        self.total_periods: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.total_return_pct: float = 0.0
        self.spy_return_pct: float = 0.0
        self.avg_alpha_pct: float = 0.0
        self.max_drawdown_pct: float = 0.0
        self.sharpe_ratio: float = 0.0
        self.win_rate_pct: float = 0.0
        self.best_month_pct: float = 0.0
        self.worst_month_pct: float = 0.0
        self.volatility_pct: float = 0.0
        self.total_transaction_cost: float = 0.0
        self.total_turnover: float = 0.0
        self.equity_curve: List[Dict[str, Any]] = []
        self.coverage: Dict[str, Any] = {}
        self.warnings: List[Dict[str, str]] = []
        self.calibration: Dict[str, Any] = {}
        self.model_scope: str = "fundamental_technical"
        self.statistics: Dict[str, Any] = {}
        self.evidence_grade: Dict[str, Any] = {}
        self.cost_sensitivity: List[Dict[str, Any]] = []
        self.backtest_scope: str = "historical_universe"
        self.requested_tickers: List[str] = []

    @property
    def num_periods(self) -> int:
        return len(self.periods)


def run_backtest(
    start: str = DEFAULT_START,
    end: Optional[str] = None,
    use_fundamentals: bool = True,
    progress_callback=None,
    *,
    selected_tickers: Optional[List[str]] = None,
    weighting: str = "equal",
    transaction_cost_bps: float = 15.0,
    initial_capital: float = 10000.0,
    persist_calibration: bool = False,
) -> BacktestResult:
    if weighting not in {"equal", "calibrated_kelly"}:
        raise ValueError("weighting must be 'equal' or 'calibrated_kelly'")
    if transaction_cost_bps < 0:
        raise ValueError("transaction_cost_bps cannot be negative")

    end_date = end or datetime.now().strftime("%Y-%m-%d")
    result = BacktestResult()
    result.model_scope = "fundamental_technical" if use_fundamentals else "technical_only"
    result.requested_tickers = sorted(set(selected_tickers or []))
    result.backtest_scope = "selected_tickers" if result.requested_tickers else "historical_universe"
    universe = HistoricalUniverse(selected_tickers=selected_tickers)
    tickers = universe.all_tickers()
    all_tickers = tickers + ["SPY", "^VIX"]

    if progress_callback:
        progress_callback(1, 0, "Fetching historical prices...")

    warmup_start = (pd.Timestamp(start) - pd.DateOffset(months=13)).strftime("%Y-%m-%d")
    fetch_end = (pd.Timestamp(end_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    price_data = fetch_price_data(all_tickers, warmup_start, fetch_end)

    if "SPY" not in price_data:
        result.periods = []
        result.warnings.append({"code": "missing_benchmark", "message": "SPY price data is unavailable."})
        return result

    if progress_callback:
        progress_callback(1, 0, "Computing rebalance dates...")

    rebalance_dates = list(pd.date_range(
        start=pd.Timestamp(start) + pd.DateOffset(months=1),
        end=pd.Timestamp(end_date),
        freq="ME",
    ))
    end_timestamp = pd.Timestamp(end_date)

    sample_tz = next((df.index.tz for df in price_data.values() if df is not None and not df.empty), None)
    reb_dates_tz = [d.tz_localize(sample_tz) if sample_tz else d for d in rebalance_dates]

    sector_map = {s["ticker"]: s["sector"] for s in STOCK_UNIVERSE}
    tier_map = {s["ticker"]: s.get("universe_tier", "core") for s in STOCK_UNIVERSE}

    fund_data: Dict[str, Optional[pd.DataFrame]] = {}
    bs_data: Dict[str, Optional[pd.DataFrame]] = {}
    if use_fundamentals:
        if progress_callback:
            progress_callback(1, 0, "Fetching quarterly fundamentals & balance sheets...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            fund_futures = {executor.submit(_fetch_single_fundamentals, ticker): ticker for ticker in tickers}
            bs_futures = {executor.submit(_fetch_single_balance_sheet, ticker): ticker for ticker in tickers}
            for future, ticker in fund_futures.items():
                try:
                    fund_data[ticker] = future.result()
                except Exception:
                    fund_data[ticker] = None
            for future, ticker in bs_futures.items():
                try:
                    bs_data[ticker] = future.result()
                except Exception:
                    bs_data[ticker] = None

    portfolio_values: List[float] = []
    spy_values: List[float] = []
    portfolio_value = initial_capital
    spy_value = initial_capital
    prior_end_weights: Dict[str, float] = {}
    calibrator = ExpandingScoreCalibrator()
    pending_observations: List[Tuple[float, bool]] = []
    technical_coverage: List[float] = []
    fundamental_coverage: List[float] = []
    eligibility_coverage: List[float] = []
    missing_exit_count = 0
    processed_rebalance_dates: List[pd.Timestamp] = []

    for i, (reb_date, reb_date_tz) in enumerate(zip(rebalance_dates, reb_dates_tz)):
        # A final partial month has no comparable forward return.
        if reb_date + pd.DateOffset(months=1) > end_timestamp:
            continue
        if progress_callback:
            progress_callback(i + 1, len(rebalance_dates),
                              f"Rebalancing {reb_date.date()}...")

        calibrator.add_many(pending_observations)
        pending_observations = []
        scored: List[Dict[str, Any]] = []
        period_universe = universe.tickers_for(reb_date.date())
        technical_count = 0
        fundamental_count = 0
        eligible_count = 0
        spy_history = price_data["SPY"][price_data["SPY"].index <= reb_date_tz]["Close"]
        vix_df = price_data.get("^VIX")
        vix_history = vix_df[vix_df.index <= reb_date_tz]["Close"] if vix_df is not None else None
        market_regime = classify_market_regime(spy_history, vix_history)

        for ticker in period_universe:
            df = price_data.get(ticker)
            if df is None or df.empty:
                continue
            hist = df[df.index <= reb_date_tz]
            if len(hist) < 50:
                continue

            tech = _score_stock_technical(hist, ticker, market_regime.get("pillar_weights"))
            if tech.get("error"):
                continue
            technical_count += 1

            tech_score = tech.get("total_score", 0)
            score = tech_score
            fund_score: Optional[float] = None
            metrics_used = 0

            if use_fundamentals and ticker in fund_data:
                qf = fund_data[ticker]
                if qf is not None:
                    fund = _extract_fundamentals_as_of(qf, bs_data.get(ticker), df, reb_date)
                    fund["ticker"] = ticker
                    fund["sector"] = sector_map.get(ticker, "Unknown")

                    from agents.fundamental_analyzer import calculate_growth_score
                    gscore_val, details, metrics_used = calculate_growth_score(fund)
                    if metrics_used > 0:
                        fundamental_count += 1
                        fund_score = gscore_val
                        score = round(gscore_val * 0.7 + tech_score * 0.3, 1)

            entry = {
                "ticker": ticker,
                "sector": sector_map.get(ticker, "Unknown"),
                "universe_tier": tier_map.get(ticker, "satellite"),
                "total_score": score,
                "tech_score": tech_score,
                "fund_score": fund_score,
                "metrics_used": metrics_used,
                "model_scope": result.model_scope,
                "price": tech.get("price"),
                "rsi_14": tech.get("rsi_14"),
                "macd_histogram": tech.get("macd_histogram"),
                "sma_20": tech.get("sma_20"),
                "sma_50": tech.get("sma_50"),
                "trend_signal": tech.get("trend_signal"),
                "signal_pillars": tech.get("signal_pillars"),
            }
            risk_metrics = calculate_risk_metrics(hist["Close"])
            risk_metrics["risk_level"] = risk_label(risk_metrics)
            entry["risk_metrics"] = risk_metrics
            entry.update(calculate_risk_adjusted_score(score, risk_metrics))
            scored.append(entry)

        if use_fundamentals:
            eligible_count = sum(
                stock.get("metrics_used", 0) >= MIN_RECOMMENDATION_METRICS
                for stock in scored
            )
        else:
            eligible_count = len(scored)

        scored.sort(key=lambda x: x.get("risk_adjusted_score", 0) or 0, reverse=True)
        picks = select_recommendations(
            scored,
            entry_threshold=market_regime.get("entry_threshold", ENTRY_THRESHOLD),
            fill_threshold=market_regime.get("fill_threshold", FILL_THRESHOLD),
            max_per_sector=MAX_PER_SECTOR,
            top_n=TOP_N,
            min_metrics=MIN_RECOMMENDATION_METRICS if use_fundamentals else None,
            max_satellite=1,
            satellite_threshold=65.0,
            score_field="risk_adjusted_score",
        )
        technical_coverage.append(technical_count / len(period_universe) if period_universe else 0.0)
        fundamental_coverage.append(fundamental_count / technical_count if technical_count else 0.0)
        eligibility_coverage.append(eligible_count / len(scored) if scored else 0.0)
        processed_rebalance_dates.append(reb_date)

        next_date_tz = reb_date_tz + pd.DateOffset(months=1)
        spy_df = price_data.get("SPY")
        spy_ret = None
        period_entry_date = None
        period_exit_date = None
        if spy_df is not None:
            spy_entry_slice = spy_df[(spy_df.index > reb_date_tz) & (spy_df.index <= next_date_tz)]
            spy_exit_slice = spy_df[(spy_df.index > reb_date_tz) & (spy_df.index <= next_date_tz)]
            if len(spy_entry_slice) >= 2 and not spy_exit_slice.empty:
                period_entry_date = _index_date(spy_entry_slice.index[0])
                period_exit_date = _index_date(spy_exit_slice.index[-1])
                spy_entry = float(spy_entry_slice["Close"].iloc[0])
                spy_exit = float(spy_exit_slice["Close"].iloc[-1])
                spy_ret = spy_exit / spy_entry - 1
        forward_returns: Dict[str, float] = {}
        all_forward_returns: Dict[str, float] = {}
        position_prices: Dict[str, Dict[str, Any]] = {}
        for stock in scored:
            ticker = stock["ticker"]
            df = price_data.get(ticker)
            entry_price = _close_on_date(df, period_entry_date)
            exit_price = _close_on_date(df, period_exit_date)
            if entry_price is None or exit_price is None:
                continue
            all_forward_returns[ticker] = exit_price / entry_price - 1
            position_prices[ticker] = {
                "entry_date": period_entry_date,
                "exit_date": period_exit_date,
                "entry_price": round(float(entry_price), 4),
                "exit_price": round(exit_price, 4),
            }
        pending_observations = [
            (stock["risk_adjusted_score"], all_forward_returns[stock["ticker"]] > 0)
            for stock in scored
            if stock["ticker"] in all_forward_returns
            and (
                not use_fundamentals
                or stock.get("metrics_used", 0) >= MIN_RECOMMENDATION_METRICS
            )
        ]
        forward_returns = {
            pick["ticker"]: all_forward_returns[pick["ticker"]]
            for pick in picks if pick["ticker"] in all_forward_returns
        }
        for pick in picks:
            ticker = pick["ticker"]
            result.tickers_picked[ticker] = result.tickers_picked.get(ticker, 0) + 1

        for pick in picks:
            sec = pick.get("sector", "Unknown")
            result.sector_counts[sec] = result.sector_counts.get(sec, 0) + 1

        weights, calibration_ready = _target_weights(
            picks,
            weighting,
            calibrator,
            target_allocation=market_regime.get("target_allocation", 0.90),
        )
        turnover = _calculate_turnover(prior_end_weights, weights)
        cost_rate = turnover * transaction_cost_bps / 10000.0
        transaction_cost = portfolio_value * cost_rate
        complete_returns = len(forward_returns) == len(picks)
        gross_return = sum(weights.get(ticker, 0.0) * value for ticker, value in forward_returns.items())
        net_return: Optional[float] = None
        if complete_returns and spy_ret is not None:
            net_return = (1.0 - cost_rate) * (1.0 + gross_return) - 1.0
            portfolio_value *= 1.0 + net_return
            portfolio_values.append(portfolio_value)
            prior_end_weights = _drift_weights(weights, forward_returns, gross_return)
            result.total_transaction_cost += transaction_cost
            result.total_turnover += turnover
        elif picks:
            missing_exit_count += len(picks) - len(forward_returns)
            transaction_cost = 0.0
            turnover = 0.0

        if spy_ret is not None and net_return is not None:
            spy_value *= 1.0 + spy_ret
            spy_values.append(spy_value)

        fund_scores = [p.get("fund_score") for p in picks if p.get("fund_score") is not None]
        positions = []
        for pick in picks:
            ticker = pick["ticker"]
            provenance = position_prices.get(ticker, {})
            positions.append({
                "ticker": ticker,
                "entry_date": provenance.get("entry_date"),
                "exit_date": provenance.get("exit_date"),
                "entry_price": provenance.get("entry_price"),
                "exit_price": provenance.get("exit_price"),
                "return_pct": round(forward_returns[ticker] * 100, 2) if ticker in forward_returns else None,
                "weight": round(weights.get(ticker, 0.0), 4),
                "model_scope": result.model_scope,
                "metrics_used": pick.get("metrics_used", 0),
                "price_source": "historical_adjusted_close",
            })
        benchmark_aligned = spy_ret is not None and (not positions or all(
            position.get("entry_date") == period_entry_date
            and position.get("exit_date") == period_exit_date
            for position in positions
        ))
        period = {
            "date": reb_date,
            "entry_date": period_entry_date,
            "exit_date": period_exit_date,
            "picks": [p["ticker"] for p in picks],
            "positions": positions,
            "model_scope": result.model_scope,
            "benchmark_alignment": "exact_period_dates" if benchmark_aligned else "unavailable_or_misaligned",
            "avg_score": round(sum(p.get("total_score", 0) for p in picks) / len(picks), 1) if picks else 0,
            "avg_model_score": round(sum(p.get("total_score", 0) for p in picks) / len(picks), 1) if picks else 0,
            "avg_risk_adjusted_score": round(sum(p.get("risk_adjusted_score", 0) for p in picks) / len(picks), 1) if picks else 0,
            "avg_risk_penalty": round(sum(p.get("risk_penalty", 0) for p in picks) / len(picks), 1) if picks else 0,
            "risk_available_count": sum(bool(p.get("risk_metrics", {}).get("available")) for p in picks),
            "avg_tech_score": round(sum(p.get("tech_score", 0) for p in picks) / len(picks), 1) if picks else 0,
            "avg_fund_score": round(sum(fund_scores) / len(fund_scores), 1) if fund_scores else None,
            "num_picks": len(picks),
            "avg_return": round(net_return * 100, 2) if net_return is not None else None,
            "gross_return": round(gross_return * 100, 2) if complete_returns and picks else None,
            "gross_return_raw": gross_return if complete_returns and picks else None,
            "net_return": round(net_return * 100, 2) if net_return is not None else None,
            "spy_return": round(spy_ret * 100, 2) if spy_ret is not None else None,
            "weights": {ticker: round(weight, 4) for ticker, weight in weights.items()},
            "cash_weight": round(1.0 - sum(weights.values()), 4),
            "turnover": round(turnover, 4),
            "turnover_raw": turnover,
            "transaction_cost": round(transaction_cost, 2),
            "portfolio_value": round(portfolio_value, 2),
            "calibration_ready": calibration_ready,
            "market_regime": market_regime,
            "coverage": {
                "universe": len(period_universe),
                "technical": technical_count,
                "fundamental": fundamental_count,
                "eligible": eligible_count,
                "ineligible": len(scored) - eligible_count,
                "eligibility_pct": round(eligible_count / len(scored) * 100, 1) if scored else 0.0,
                "minimum_metrics": MIN_RECOMMENDATION_METRICS if use_fundamentals else None,
                "scored": len(scored),
            },
        }

        if period["avg_return"] is not None and period["spy_return"] is not None and benchmark_aligned:
            period["alpha"] = round(period["avg_return"] - period["spy_return"], 2)
            period["beat_spy"] = period["avg_return"] > period["spy_return"]
            result.total_periods += 1
            if period["beat_spy"]:
                result.wins += 1
            else:
                result.losses += 1

        result.periods.append(period)
        if net_return is not None:
            result.equity_curve.append({
                "date": reb_date,
                "portfolio": round(portfolio_value, 2),
                "spy": round(spy_value, 2),
            })

    calibrator.add_many(pending_observations)
    universe_coverage = universe.coverage_for(processed_rebalance_dates)
    result.coverage = {
        "requested_tickers": len(tickers),
        "tickers_with_prices": sum(ticker in price_data for ticker in tickers),
        "avg_technical_pct": round(float(np.mean(technical_coverage)) * 100, 1) if technical_coverage else 0.0,
        "avg_fundamental_pct": round(float(np.mean(fundamental_coverage)) * 100, 1) if fundamental_coverage else 0.0,
        "avg_eligibility_pct": round(float(np.mean(eligibility_coverage)) * 100, 1) if eligibility_coverage else 0.0,
        "minimum_metrics": MIN_RECOMMENDATION_METRICS if use_fundamentals else None,
        "universe": universe_coverage,
        "universe_status": universe.status(),
        "missing_exit_prices": missing_exit_count,
    }
    result.calibration = calibrator.snapshot()
    result.calibration["model_scope"] = result.model_scope
    result.calibration["quality_gates"] = {
        "historical_universe_coverage_pct": universe_coverage["coverage_pct"],
        "technical_coverage_pct": result.coverage["avg_technical_pct"],
        "eligibility_coverage_pct": result.coverage["avg_eligibility_pct"],
        "missing_exit_prices": missing_exit_count,
    }
    result.calibration["persisted"] = False
    calibration_eligible = (
        not universe.uses_current_universe_fallback
        and universe_coverage["coverage_pct"] == 100.0
        and use_fundamentals
        and result.coverage["avg_technical_pct"] >= 80
        and result.coverage["avg_eligibility_pct"] >= 50
        and missing_exit_count == 0
    )
    result.calibration["eligible_for_live"] = calibration_eligible
    if persist_calibration and calibration_eligible:
        result.calibration["persisted"] = save_calibration_snapshot(
            result.calibration,
            as_of=end_date,
        )
    elif persist_calibration and result.calibration.get("ready"):
        result.warnings.append({
            "code": "calibration_not_persisted",
            "message": "Calibration was not saved for live sizing because universe or price coverage quality checks failed.",
        })
    if universe.uses_current_universe_fallback:
        result.warnings.append({
            "code": "selected_ticker_bias" if result.backtest_scope == "selected_tickers" else "survivorship_bias",
            "message": (
                "Selected tickers are tested retrospectively without verified historical constituent membership."
                if result.backtest_scope == "selected_tickers"
                else "Historical universe snapshots are unavailable; results use today's stock universe and may contain survivorship bias."
            ),
        })
    elif universe_coverage["coverage_pct"] < 100:
        result.warnings.append({
            "code": "historical_universe_gap",
            "message": "Historical universe snapshots do not cover every backtest period; uncovered periods contain no candidates.",
        })
    if use_fundamentals and result.coverage["avg_fundamental_pct"] < 50:
        result.warnings.append({
            "code": "low_fundamental_coverage",
            "message": "Point-in-time fundamental coverage is below 50%; candidates without four metrics are excluded from picks.",
        })
    if missing_exit_count:
        result.warnings.append({
            "code": "missing_exit_prices",
            "message": f"{missing_exit_count} selected positions lacked a forward exit price; affected months were excluded.",
        })
    if weighting == "calibrated_kelly" and not result.calibration.get("ready"):
        result.warnings.append({
            "code": "calibration_warmup",
            "message": "Calibration did not reach the minimum sample; equal weights were used during warm-up.",
        })

    _compute_aggregate_metrics(result, portfolio_values, spy_values, initial_capital)
    _attach_statistical_evidence(result)
    return result


def _target_weights(
    picks: List[Dict[str, Any]],
    weighting: str,
    calibrator: ExpandingScoreCalibrator,
    target_allocation: float = 0.90,
) -> Tuple[Dict[str, float], bool]:
    if not picks:
        return {}, False
    if weighting == "calibrated_kelly":
        probabilities = {
            pick["ticker"]: calibrator.probability(
                pick.get("risk_adjusted_score", pick.get("total_score", 0)) or 0
            )
            for pick in picks
        }
        if all(probability is not None for probability in probabilities.values()):
            raw = {
                ticker: kelly_fraction(probability, DEFAULT_UPISDE_PCT, DEFAULT_STOP_LOSS_PCT)
                for ticker, probability in probabilities.items()
                if probability is not None
            }
            if any(raw.values()):
                return normalize_capped_weights(raw, target_allocation=target_allocation), True
    return normalize_capped_weights(
        {pick["ticker"]: 1.0 for pick in picks},
        target_allocation=target_allocation,
    ), False


def _calculate_turnover(current: Dict[str, float], target: Dict[str, float]) -> float:
    tickers = set(current) | set(target)
    stock_turnover = sum(abs(target.get(ticker, 0.0) - current.get(ticker, 0.0)) for ticker in tickers)
    current_cash = 1.0 - sum(current.values())
    target_cash = 1.0 - sum(target.values())
    return 0.5 * (stock_turnover + abs(target_cash - current_cash))


def _drift_weights(
    target: Dict[str, float],
    forward_returns: Dict[str, float],
    portfolio_return: float,
) -> Dict[str, float]:
    denominator = 1.0 + portfolio_return
    if denominator <= 0:
        return {}
    return {
        ticker: weight * (1.0 + forward_returns.get(ticker, 0.0)) / denominator
        for ticker, weight in target.items()
    }


def _compute_aggregate_metrics(
    result: BacktestResult,
    portfolio_values: List[float],
    spy_values: List[float],
    initial_capital: float = 10000.0,
) -> None:
    valid = [p for p in result.periods if p.get("avg_return") is not None]
    if not valid:
        return

    spy_valid = [p for p in valid if p.get("spy_return") is not None]

    aligned_periods = result.wins + result.losses
    result.win_rate_pct = round(result.wins / aligned_periods * 100, 1) if aligned_periods > 0 else 0.0

    returns = [p["avg_return"] for p in valid if p["avg_return"] is not None]
    spy_returns = [p["spy_return"] for p in spy_valid if p["spy_return"] is not None]

    alpha_values = [p["alpha"] for p in valid if p.get("alpha") is not None]
    result.avg_alpha_pct = round(float(np.mean(alpha_values)), 2) if alpha_values else 0.0

    if portfolio_values:
        result.total_return_pct = round((portfolio_values[-1] / initial_capital - 1) * 100, 2)

    if spy_values:
        result.spy_return_pct = round((spy_values[-1] / initial_capital - 1) * 100, 2)

    if returns:
        result.best_month_pct = round(max(returns), 2)
        result.worst_month_pct = round(min(returns), 2)

        returns_arr = np.array(returns)
        result.volatility_pct = round(float(np.std(returns_arr, ddof=1) * math.sqrt(12)), 2) if len(returns_arr) > 1 else 0.0

        if len(returns_arr) > 1 and np.std(returns_arr, ddof=1) > 0:
            risk_free = 0.0
            excess = np.mean(returns_arr) - risk_free
            result.sharpe_ratio = round(float(excess / np.std(returns_arr, ddof=1) * math.sqrt(12)), 2)

        if portfolio_values:
            peak = initial_capital
            max_dd = 0.0
            for v in portfolio_values:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            result.max_drawdown_pct = round(max_dd, 2)

    result.total_periods = len(valid)


def _attach_statistical_evidence(result: BacktestResult) -> None:
    periods = [period for period in result.periods if period.get("net_return") is not None]
    aligned = [period for period in periods if period.get("alpha") is not None]
    returns = [float(period["net_return"]) for period in periods]
    alpha = [float(period["alpha"]) for period in aligned]
    samples = [
        {
            "sample_id": str(period.get("date")),
            "entry_date": period.get("entry_date"),
            "exit_date": period.get("exit_date"),
            "alpha_pct": period.get("alpha"),
        }
        for period in aligned
    ]
    effective = effective_sample_size(samples, "alpha_pct")
    block_length = max(1, min(len(aligned), int(round(len(aligned) ** (1 / 3))))) if aligned else 1
    return_ci = bootstrap_ci(returns, block_length=block_length)
    alpha_ci = bootstrap_ci(alpha, block_length=block_length)
    win_rate_ci = bootstrap_ci(alpha, block_length=block_length, statistic="win_rate")
    result.statistics = {
        "version": STATISTICS_VERSION,
        "method": "monthly_moving_block_percentile",
        "seed": DEFAULT_BOOTSTRAP_SEED,
        "iterations": DEFAULT_BOOTSTRAP_ITERATIONS,
        "block_length": block_length,
        "effective_periods": effective,
        "net_return_ci": return_ci,
        "alpha_ci": alpha_ci,
        "win_rate_ci": win_rate_ci,
        "benchmark_alignment": {
            "aligned": len(aligned),
            "eligible": len(periods),
            "pct": round(len(aligned) / len(periods) * 100, 1) if periods else 0.0,
        },
    }
    result.cost_sensitivity = []
    for cost_bps in (0, 15, 30, 60):
        scenario_returns = []
        scenario_alpha = []
        for period in periods:
            gross = period.get("gross_return_raw")
            turnover = period.get("turnover_raw")
            if gross is None or turnover is None:
                continue
            net = ((1 - float(turnover) * cost_bps / 10000) * (1 + float(gross)) - 1) * 100
            scenario_returns.append(net)
            if period.get("spy_return") is not None:
                scenario_alpha.append(net - float(period["spy_return"]))
        result.cost_sensitivity.append({
            "transaction_cost_bps": cost_bps,
            "periods": len(scenario_returns),
            "average_net_return_pct": round(float(np.mean(scenario_returns)), 3) if scenario_returns else None,
            "average_alpha_pct": round(float(np.mean(scenario_alpha)), 3) if scenario_alpha else None,
        })

    universe_coverage = result.coverage.get("universe", {}).get("coverage_pct", 0.0)
    n_eff = float(effective.get("effective", 0))
    positive_alpha = alpha_ci.get("lower") is not None and alpha_ci["lower"] > 0
    positive_return = return_ci.get("lower") is not None and return_ci["lower"] > 0
    reasons = []
    if result.backtest_scope == "selected_tickers":
        reasons.append("ticker_specific_selection_bias")
        reasons.append("historical_membership_unverified")
    elif universe_coverage < 100:
        reasons.append("historical_universe_incomplete")
    if result.model_scope != "fundamental_technical":
        reasons.append("model_scope_not_full")
    if result.statistics["benchmark_alignment"]["pct"] < 100:
        reasons.append("benchmark_alignment_incomplete")
    if n_eff < 8 or len(aligned) < 12:
        level = "insufficient"
        reasons.append("effective_sample_too_small")
    elif reasons or not positive_alpha:
        level = "low"
        if not positive_alpha:
            reasons.append(_alpha_interval_reason(alpha_ci))
    elif n_eff >= 24 and positive_return:
        level = "high"
    else:
        level = "medium"
    result.evidence_grade = {
        "level": level,
        "effective_periods": round(n_eff, 2),
        "raw_periods": len(aligned),
        "reason_codes": reasons,
    }


def _alpha_interval_reason(interval: Dict[str, Any]) -> str:
    if not interval.get("available") or interval.get("lower") is None or interval.get("upper") is None:
        return "alpha_interval_unavailable"
    lower, upper = float(interval["lower"]), float(interval["upper"])
    if upper < 0:
        return "alpha_interval_below_zero"
    if lower <= 0 <= upper:
        return "alpha_interval_includes_zero"
    return "alpha_interval_not_positive"


def format_backtest_summary(result: BacktestResult) -> Dict[str, Any]:
    return {
        "total_periods": result.total_periods,
        "win_rate": result.win_rate_pct,
        "wins": result.wins,
        "losses": result.losses,
        "total_return": result.total_return_pct,
        "spy_return": result.spy_return_pct,
        "avg_alpha": result.avg_alpha_pct,
        "max_drawdown": result.max_drawdown_pct,
        "sharpe": result.sharpe_ratio,
        "best_month": result.best_month_pct,
        "worst_month": result.worst_month_pct,
        "volatility": result.volatility_pct,
        "total_transaction_cost": round(result.total_transaction_cost, 2),
        "total_turnover": round(result.total_turnover, 4),
        "equity_curve": result.equity_curve,
        "coverage": result.coverage,
        "warnings": result.warnings,
        "calibration": result.calibration,
        "model_scope": result.model_scope,
        "backtest_scope": result.backtest_scope,
        "requested_tickers": result.requested_tickers,
        "statistics": result.statistics,
        "historical_evidence_grade": result.evidence_grade,
        "cost_sensitivity": result.cost_sensitivity,
        "periods": result.periods,
        "tickers_picked": dict(sorted(result.tickers_picked.items(), key=lambda x: x[1], reverse=True)[:20]),
        "sector_counts": result.sector_counts,
    }
