from typing import Any, Dict, List, Optional, Tuple
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from agents.risk_analyzer import calculate_portfolio_risk, fetch_risk_metrics
from backtesting.calibration import load_calibration_snapshot, probability_from_snapshot
from agents.reflection_agent import (
    capture_thesis,
    log_outcome,
    complete_trade_cycle,
)


DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
JOURNAL_PATH = os.path.join(DATA_DIR, "trade_journal.json")
PORTFOLIO_PATH = os.path.join(DATA_DIR, "portfolio_state.json")

DEFAULT_STOP_LOSS_PCT = 0.10
DEFAULT_UPISDE_PCT = 0.15
MAX_KELLY_FRACTION = 0.25
TOTAL_ALLOCATION = 0.90
CORR_THRESHOLD = 0.80


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def kelly_fraction(win_prob: float, upside_pct: float, downside_pct: float = DEFAULT_STOP_LOSS_PCT) -> float:
    if downside_pct <= 0 or upside_pct <= 0:
        return 0
    b = upside_pct / downside_pct
    q = 1 - win_prob
    if b <= 0:
        return 0
    f = (win_prob * b - q) / b
    return max(0.0, min(f, MAX_KELLY_FRACTION))


def normalize_capped_weights(
    weights: Dict[str, float],
    target_allocation: float = TOTAL_ALLOCATION,
    max_weight: float = MAX_KELLY_FRACTION,
) -> Dict[str, float]:
    """Scale positive weights while preserving a hard position cap and cash when needed."""
    active = {ticker: max(0.0, weight) for ticker, weight in weights.items() if weight > 0}
    if not active:
        return {ticker: 0.0 for ticker in weights}

    target = min(target_allocation, max_weight * len(active))
    allocated: Dict[str, float] = {ticker: 0.0 for ticker in weights}
    remaining = set(active)
    remaining_target = target

    while remaining and remaining_target > 0:
        total = sum(active[ticker] for ticker in remaining)
        if total <= 0:
            break
        capped = []
        for ticker in remaining:
            proposed = remaining_target * active[ticker] / total
            if proposed >= max_weight:
                allocated[ticker] = max_weight
                remaining_target -= max_weight
                capped.append(ticker)
        if not capped:
            for ticker in remaining:
                allocated[ticker] = remaining_target * active[ticker] / total
            break
        remaining.difference_update(capped)

    return {ticker: round(allocated[ticker], 4) for ticker in weights}


def calculate_portfolio_weights(
    recommendations: List[Dict[str, Any]],
    calibration: Optional[Dict[str, Any]] = None,
    target_allocation: float = TOTAL_ALLOCATION,
) -> Tuple[Dict[str, float], str]:
    if not recommendations:
        return {}, "equal"

    snapshot = calibration if calibration is not None else load_calibration_snapshot()
    probabilities = {
        recommendation["ticker"]: probability_from_snapshot(
            recommendation.get("risk_adjusted_score", recommendation.get("total_score", 0)) or 0,
            snapshot,
        ) if snapshot else None
        for recommendation in recommendations
    }
    if not snapshot or any(probability is None for probability in probabilities.values()):
        equal = {recommendation["ticker"]: 1.0 for recommendation in recommendations}
        return normalize_capped_weights(equal, target_allocation=target_allocation), "equal"

    raw_weights: Dict[str, float] = {}
    for recommendation in recommendations:
        ticker = recommendation["ticker"]
        price = recommendation.get("price")
        target = recommendation.get("target_mean_price")
        upside = (target / price) - 1 if price and target and target > price else DEFAULT_UPISDE_PCT
        beta = recommendation.get("beta")
        downside = min(DEFAULT_STOP_LOSS_PCT * beta, 0.25) if beta and beta > 0 else DEFAULT_STOP_LOSS_PCT
        raw_weights[ticker] = kelly_fraction(probabilities[ticker], upside, downside)

    if not any(raw_weights.values()):
        equal = {recommendation["ticker"]: 1.0 for recommendation in recommendations}
        return normalize_capped_weights(equal, target_allocation=target_allocation), "equal"
    return normalize_capped_weights(raw_weights, target_allocation=target_allocation), "calibrated_kelly"


def compute_correlations(tickers: List[str], period: str = "1y") -> Tuple[Optional[pd.DataFrame], List[Tuple[str, str, float]], pd.DataFrame]:
    valid = [t for t in tickers if t]
    if not valid:
        return None, [], pd.DataFrame()

    try:
        data = yf.download(valid, period=period, progress=False, auto_adjust=True)
        if data.empty:
            return None, [], pd.DataFrame()
        close = data["Close"] if "Close" in data.columns else data
        if isinstance(close, pd.Series):
            close = close.to_frame(name=valid[0])
        returns = close.pct_change().dropna()
        if returns.empty or returns.shape[1] < 2:
            return None, [], close
        corr = returns.corr()
        high_pairs: List[Tuple[str, str, float]] = []
        for i in range(len(corr.columns)):
            for j in range(i + 1, len(corr.columns)):
                val = corr.iloc[i, j]
                if val >= CORR_THRESHOLD:
                    high_pairs.append((corr.columns[i], corr.columns[j], round(val, 3)))
        return corr, high_pairs, close
    except Exception:
        return None, [], pd.DataFrame()


def _load_journal() -> List[Dict[str, Any]]:
    _ensure_data_dir()
    if os.path.exists(JOURNAL_PATH):
        try:
            with open(JOURNAL_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_journal(entries: List[Dict[str, Any]]) -> None:
    _ensure_data_dir()
    with open(JOURNAL_PATH, "w") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _load_portfolio_state() -> Dict[str, Any]:
    _ensure_data_dir()
    if os.path.exists(PORTFOLIO_PATH):
        try:
            with open(PORTFOLIO_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_portfolio_state(state: Dict[str, Any]) -> None:
    _ensure_data_dir()
    with open(PORTFOLIO_PATH, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def log_trade(
    ticker: str,
    action: str,
    price: float,
    shares: int,
    reason: str = "",
    thesis: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log a trade to the journal. Optionally attach thesis data for later reflection.
    
    Args:
        ticker: stock symbol
        action: "buy", "sell", "reset"
        price: trade price
        shares: quantity
        reason: human-readable explanation
        thesis: optional thesis dict from reflection_agent.capture_thesis()
    """
    entries = _load_journal()
    entry = {
        "date": datetime.now().isoformat(),
        "ticker": ticker,
        "action": action,
        "price": round(price, 2),
        "shares": shares,
        "reason": reason,
    }
    if thesis:
        entry["thesis"] = thesis
    entries.append(entry)
    _save_journal(entries)


def _reflect_on_exits(
    exited_tickers: List[str],
    old_positions: Dict[str, Any],
    journal: List[Dict[str, Any]],
) -> None:
    """
    Trigger reflection on positions that were closed (exited from the portfolio).
    
    For each exit, try to find the entry in the journal (with thesis), compute outcome,
    and run the LLM reflection cycle. This is the core self-learning loop.
    """
    for ticker in exited_tickers:
        old = old_positions.get(ticker, {})
        entry_price = old.get("entry_price")
        entry_date = old.get("entry_date")
        
        if not entry_price or not entry_date:
            continue
        
        # Find the entry in the journal with thesis data
        entry_record = None
        for record in reversed(journal):
            if (record.get("ticker") == ticker and 
                record.get("action") == "buy" and 
                "thesis" in record):
                entry_record = record
                break
        
        if not entry_record or "thesis" not in entry_record:
            # No thesis captured at entry, can't reflect
            continue
        
        # Fetch current price for exit
        try:
            ticker_obj = yf.Ticker(ticker)
            hist = ticker_obj.history(period="1d")
            if hist.empty:
                continue
            exit_price = float(hist["Close"].iloc[-1])
        except Exception:
            continue
        
        # Log the exit to journal
        journal.append({
            "date": datetime.now().isoformat(),
            "ticker": ticker,
            "action": "sell",
            "price": round(exit_price, 2),
            "shares": 0,
            "reason": "Rebalance exit (dropped from top recommendations)",
        })
        
        # Build outcome
        outcome = log_outcome(
            ticker=ticker,
            exit_price=exit_price,
            exit_trigger="rebalance",
            entry_date=entry_date,
            entry_price=entry_price,
            narrative=f"Position closed during rebalancing. Entry ${entry_price:.2f} → Exit ${exit_price:.2f}.",
        )
        
        # Complete the reflection cycle (LLM reflect + save + append to TRADE_JOURNAL.md)
        complete_trade_cycle(entry_record["thesis"], outcome, append_to_markdown=True)


def build_portfolio(
    recommendations: List[Dict[str, Any]],
    total_capital: float = 100000.0,
    target_allocation: float = TOTAL_ALLOCATION,
    previous_state: Optional[Dict[str, Any]] = None,
    journal: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    saved = previous_state or {}
    updated_journal = list(journal or [])
    old_positions = saved.get("positions", {})
    all_tickers = [r["ticker"] for r in recommendations]
    corr, high_pairs, close_history = compute_correlations(all_tickers)

    weights, weighting_method = calculate_portfolio_weights(recommendations, target_allocation=target_allocation)

    # Detect exits: tickers that were held but are no longer recommended (or dropped to zero weight).
    # Each exit triggers a reflection cycle so the system learns from the closed trade.
    new_tickers = {r["ticker"] for r in recommendations if weights.get(r["ticker"], 0) > 0}
    exited_tickers = [t for t in old_positions if t not in new_tickers]
    if exited_tickers:
        _reflect_on_exits(exited_tickers, old_positions, updated_journal)

    positions: List[Dict[str, Any]] = []
    for r in recommendations:
        ticker = r["ticker"]
        price = r.get("price")
        if not price or price <= 0:
            continue

        weight = weights.get(ticker, 0)
        alloc = total_capital * weight
        shares = int(alloc / price)

        old = old_positions.get(ticker, {})
        entry_price = old.get("entry_price", price)
        entry_date = old.get("entry_date", datetime.now().isoformat())

        target = r.get("target_mean_price")
        beta = r.get("beta")
        if beta and beta > 0:
            stop_loss_pct = min(DEFAULT_STOP_LOSS_PCT * beta, 0.25)
        else:
            stop_loss_pct = DEFAULT_STOP_LOSS_PCT
        stop_loss = round(entry_price * (1 - stop_loss_pct), 2)

        if not old:
            # Capture the full scoring thesis at entry so we can reflect when the position closes.
            thesis = capture_thesis(
                ticker=ticker,
                entry_price=price,
                recommendation=r,
                regime=r.get("regime"),
                position_size_pct=weight,
                stop_price=stop_loss,
                target_price=target,
            )
            updated_journal.append({
                "date": datetime.now().isoformat(), "ticker": ticker, "action": "buy",
                "price": round(price, 2), "shares": shares,
                "reason": f"Portfolio allocation {weight*100:.1f}%",
                "thesis": thesis,
            })

        pnl = (price - entry_price) * shares
        pnl_pct = ((price / entry_price) - 1) * 100 if entry_price > 0 else 0

        positions.append({
            "ticker": ticker,
            "name": r.get("longName") or r.get("name_cn", ticker),
            "sector": r.get("sector", ""),
            "entry_price": entry_price,
            "entry_date": entry_date,
            "current_price": price,
            "shares": shares,
            "market_value": round(price * shares, 2),
            "weight": weight,
            "kelly_pct": round(weight * 100, 1),
            "weighting_method": weighting_method,
            "stop_loss": stop_loss,
            "target_price": target,
            "total_score": r.get("total_score", 0),
            "risk_adjusted_score": r.get("risk_adjusted_score", r.get("total_score", 0)),
            "risk_penalty": r.get("risk_penalty", 0),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "stop_hit": price <= stop_loss,
            "risk_metrics": r.get("risk_metrics", {"available": False, "risk_level": "unknown"}),
        })

    positions.sort(key=lambda p: p["weight"], reverse=True)
    total_invested = sum(p["entry_price"] * p["shares"] for p in positions)
    holdings_value = sum(p["market_value"] for p in positions)
    total_pnl = holdings_value - total_invested
    cash = total_capital - total_invested
    portfolio_value = cash + holdings_value
    for position in positions:
        position["actual_weight"] = position["market_value"] / portfolio_value if portfolio_value > 0 else 0
    portfolio_risk = calculate_portfolio_risk(
        close_history,
        {position["ticker"]: position["market_value"] for position in positions if position["market_value"] > 0},
        cash=cash,
    )

    new_state = {
        "positions": {p["ticker"]: {"entry_price": p["entry_price"], "entry_date": p["entry_date"]} for p in positions},
        "capital": total_capital,
        "updated_at": datetime.now().isoformat(),
    }
    missing_risk = [position["ticker"] for position in positions if not position["risk_metrics"].get("available")]
    if missing_risk:
        risk_by_ticker = fetch_risk_metrics(missing_risk)
        for position in positions:
            if position["ticker"] in risk_by_ticker:
                position["risk_metrics"] = risk_by_ticker[position["ticker"]]

    return {
        "positions": positions,
        "total_capital": total_capital,
        "total_invested": round(total_invested, 2),
        "portfolio_value": round(portfolio_value, 2),
        "holdings_value": round(holdings_value, 2),
        "cash": round(cash, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / total_invested * 100) if total_invested > 0 else 0, 2),
        "high_corr_pairs": high_pairs,
        "correlation_df": corr,
        "portfolio_risk": portfolio_risk,
        "num_positions": len(positions),
        "weighting_method": weighting_method,
        "session_state": new_state,
        "journal": updated_journal,
    }


def get_journal() -> List[Dict[str, Any]]:
    return _load_journal()


def reset_portfolio(capital: float = 100000.0) -> Dict[str, Any]:
    _ensure_data_dir()
    state = {
        "positions": {},
        "capital": capital,
        "updated_at": datetime.now().isoformat(),
    }
    _save_portfolio_state(state)
    entries = _load_journal()
    if entries:
        entries.append({
            "date": datetime.now().isoformat(),
            "ticker": "PORTFOLIO",
            "action": "reset",
            "price": 0,
            "shares": 0,
            "reason": "Portfolio reset",
        })
        _save_journal(entries)
    return state
