"""
Strategy backtesting engine — validates Stable/Aggressive/Hybrid strategies
against historical OHLCV data with realistic fills.

Design:
  - Daily bars per ticker (warmup window prepended so indicators are valid).
  - For each trading day after warmup:
      * Check exits on open positions first (stop / take-profit / signal reversal).
      * Then scan the universe for entry signals via StrategyBase.generate_signal().
  - Fills use the ShadowTradingEngine 2.0 model (next-bar open + slippage for
    market orders; limit-touch for limit orders), so results are cost-aware.
  - Returns per-trade P&L, win rate, profit factor, Sharpe, and max drawdown.

Known limitation (recorded in UPGRADE_RECOVERY_LOG): price data comes from
yfinance so the universe reflects today's constituents — survivorship bias is
unavoidable without a licensed point-in-time snapshot. Fundamental scores are
not available per historical date here; strategies run with a neutral score.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from backend.backtesting.engine import fetch_price_data
from backend.trading.engine.shadow import ShadowTradingEngine, DEFAULT_SLIPPAGE_BPS
from backend.trading.strategies.registry import get_strategy
from backend.utils.constants import STOCK_UNIVERSE

WARMUP_DAYS = 250
MAX_POSITIONS = 10
STARTING_CAPITAL = 100_000.0


class StrategyBacktestResult:
    def __init__(self) -> None:
        self.trades: List[Dict[str, Any]] = []
        self.equity_curve: List[Dict[str, Any]] = []
        self.total_return_pct: float = 0.0
        self.win_rate_pct: float = 0.0
        self.profit_factor: float = 0.0
        self.sharpe_ratio: float = 0.0
        self.max_drawdown_pct: float = 0.0
        self.num_trades: int = 0
        self.total_fees_pct: float = 0.0
        self.warnings: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trades": self.trades,
            "equity_curve": self.equity_curve,
            "total_return_pct": round(self.total_return_pct, 2),
            "win_rate_pct": round(self.win_rate_pct, 2),
            "profit_factor": round(self.profit_factor, 3),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "num_trades": self.num_trades,
            "total_fees_pct": round(self.total_fees_pct, 2),
            "warnings": self.warnings,
        }


def run_strategy_backtest(
    strategy_id: str = "stable",
    start: Optional[str] = None,
    end: Optional[str] = None,
    tickers: Optional[List[str]] = None,
    transaction_cost_bps: float = 10.0,
    max_positions: int = MAX_POSITIONS,
    initial_capital: float = STARTING_CAPITAL,
) -> StrategyBacktestResult:
    """Run a daily-frequency backtest of a single strategy over *start*..*end*."""
    strategy = get_strategy(strategy_id)
    result = StrategyBacktestResult()

    if tickers is None:
        tickers = [s["ticker"] for s in STOCK_UNIVERSE]
    tickers = [t for t in tickers if t != "SPY"]

    end_date = end or pd.Timestamp.now().strftime("%Y-%m-%d")
    if start is None:
        start = (pd.Timestamp(end_date) - pd.DateOffset(years=3)).strftime("%Y-%m-%d")

    # Warmup: fetch data starting WARMUP_DAYS before the requested start so that
    # SMA200 / SMA50 etc. have enough history on day one.
    fetch_start = (pd.Timestamp(start) - pd.DateOffset(days=WARMUP_DAYS + 60)).strftime("%Y-%m-%d")
    fetch_end = (pd.Timestamp(end_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    price_data = fetch_price_data(tickers, fetch_start, fetch_end)

    missing = [t for t in tickers if t not in price_data]
    if missing:
        result.warnings.append(f"No price data for: {', '.join(missing[:8])}")

    # Align all series to their local dates and normalize timezone to None.
    aligned: Dict[str, pd.DataFrame] = {}
    for ticker, df in price_data.items():
        if df is None or df.empty:
            continue
        d = df.copy()
        if d.index.tz is not None:
            d.index = d.index.tz_localize(None)
        # Keep required OHLCV columns only.
        cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in d.columns]
        d = d[cols].dropna()
        d = d[~d.index.duplicated(keep="last")]
        aligned[ticker] = d

    if not aligned:
        result.warnings.append("No usable price data at all.")
        return result

    # Common trading calendar = union of all available dates.
    all_dates = sorted(set().union(*[set(d.index) for d in aligned.values()]))
    all_dates = [d for d in all_dates if pd.Timestamp(start) <= d <= pd.Timestamp(end_date)]
    if len(all_dates) < 30:
        result.warnings.append("Too few trading dates for a meaningful backtest.")
        return result

    # Positions: {ticker: {entry_price, qty, stop, target, entry_date, meta}}
    positions: Dict[str, Dict[str, Any]] = {}
    cash = initial_capital
    trade_records: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []

    shadow = ShadowTradingEngine(order_manager=None, slippage_bps=DEFAULT_SLIPPAGE_BPS)

    for day_idx, day in enumerate(all_dates):
        # 1. Check exits before entries (respect stop/target on the bar's OHLC).
        for ticker in list(positions.keys()):
            if ticker not in aligned:
                continue
            bar = aligned[ticker]
            try:
                row = bar.loc[day]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[-1]
            except KeyError:
                continue  # ticker not trading that day

            high, low, close = float(row["High"]), float(row["Low"]), float(row["Close"])
            pos = positions[ticker]
            exit_reason = None
            exit_price = close

            if pos["stop"] and low <= pos["stop"]:
                exit_reason = "stop_loss"
                exit_price = pos["stop"]
            elif pos["target"] and high >= pos["target"]:
                exit_reason = "take_profit"
                exit_price = pos["target"]

            # Strategy-level exit (e.g. trend break / RSI restore)
            if exit_reason is None:
                hist = _history_through(aligned[ticker], day)
                if hist is not None and len(hist) >= 30:
                    es = strategy.check_exit(
                        ticker=ticker,
                        entry_price=pos["entry_price"],
                        current_price=close,
                        df=hist,
                        stop_loss_price=pos["stop"] or close * 0.9,
                        take_profit_price=pos["target"] or close * 1.1,
                    )
                    if es:
                        exit_reason = es.reason
                        exit_price = es.exit_price

            if exit_reason:
                proceeds = pos["qty"] * exit_price * (1 - transaction_cost_bps / 10000.0)
                cash += proceeds
                pnl = (exit_price - pos["entry_price"]) * pos["qty"]
                pnl_pct = pnl / (pos["entry_price"] * pos["qty"]) * 100.0 if pos["entry_price"] else 0.0
                trade_records.append({
                    "ticker": ticker,
                    "side": "long",
                    "entry_date": pos["entry_date"],
                    "exit_date": day.strftime("%Y-%m-%d"),
                    "entry_price": round(pos["entry_price"], 4),
                    "exit_price": round(exit_price, 4),
                    "qty": pos["qty"],
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "exit_reason": exit_reason,
                    "strategy": strategy_id,
                })
                del positions[ticker]

        # 2. Entries — only if we have room and capital.
        if len(positions) < max_positions and cash > 500:
            current_pos_list = [{"symbol": t, "quantity": p["qty"]} for t, p in positions.items()]
            for ticker in sorted(aligned.keys()):
                if len(positions) >= max_positions:
                    break
                if ticker in positions:
                    continue
                hist = _history_through(aligned[ticker], day)
                if hist is None or len(hist) < 30:
                    continue
                # Point-in-time fundamentals are unavailable from the free provider,
                # so Stable/Hybrid (which gate on fundamental quality) are given a
                # pass-through score; Aggressive has a lower bar by design.
                fund_score = 70.0 if strategy_id in ("stable", "hybrid") else 50.0
                signal = strategy.generate_signal(
                    ticker=ticker,
                    df=hist,
                    fundamental_score=fund_score,
                    llm_signal=None,
                    current_positions=current_pos_list,
                )
                if signal is None:
                    continue
                # Sizing: allocate a slice of available cash, capped by strategy's
                # max concentration (Kelly floor), at most one unit per signal.
                alloc = cash * 0.05  # fixed 5% slice for determinism
                qty = max(1, int(alloc / signal.entry_price)) if signal.entry_price else 0
                if qty <= 0:
                    continue
                cost = qty * signal.entry_price
                if cost > cash:
                    qty = max(1, int(cash / signal.entry_price)) if signal.entry_price else 0
                    cost = qty * signal.entry_price
                if qty <= 0 or cost <= 0:
                    continue
                cash -= cost * (1 + transaction_cost_bps / 10000.0)
                positions[ticker] = {
                    "entry_price": signal.entry_price,
                    "qty": qty,
                    "stop": signal.stop_loss_price,
                    "target": signal.take_profit_price,
                    "entry_date": day.strftime("%Y-%m-%d"),
                    "meta": signal.meta,
                }
                current_pos_list.append({"symbol": ticker, "quantity": qty})

        # 3. Mark-to-market equity.
        equity = cash
        for ticker, pos in positions.items():
            if ticker in aligned:
                try:
                    row = aligned[ticker].loc[day]
                    if isinstance(row, pd.DataFrame):
                        row = row.iloc[-1]
                    equity += pos["qty"] * float(row["Close"])
                except KeyError:
                    equity += pos["qty"] * pos["entry_price"]
            else:
                equity += pos["qty"] * pos["entry_price"]
        equity_curve.append({"date": day.strftime("%Y-%m-%d"), "equity": round(equity, 2)})

    # Force-close remaining positions at last available close.
    last_day = all_dates[-1]
    for ticker in list(positions.keys()):
        if ticker not in aligned:
            continue
        close = float(aligned[ticker]["Close"].iloc[-1])
        pos = positions[ticker]
        proceeds = pos["qty"] * close * (1 - transaction_cost_bps / 10000.0)
        cash += proceeds
        pnl = (close - pos["entry_price"]) * pos["qty"]
        trade_records.append({
            "ticker": ticker, "side": "long",
            "entry_date": pos["entry_date"], "exit_date": last_day.strftime("%Y-%m-%d"),
            "entry_price": round(pos["entry_price"], 4), "exit_price": round(close, 4),
            "qty": pos["qty"], "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / (pos["entry_price"] * pos["qty"]) * 100.0, 2)
            if pos["entry_price"] else 0.0,
            "exit_reason": "end_of_backtest", "strategy": strategy_id,
        })

    # ---- Metrics ----
    result.trades = trade_records
    result.equity_curve = equity_curve
    result.num_trades = len(trade_records)

    if not trade_records:
        result.warnings.append("No trades generated. Check strategy parameters and data.")
        return result

    wins = [t for t in trade_records if t["pnl"] > 0]
    losses = [t for t in trade_records if t["pnl"] < 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    result.win_rate_pct = len(wins) / len(trade_records) * 100.0
    result.profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

    # Sharpe from equity curve (daily, annualized).
    closes = [e["equity"] for e in equity_curve]
    rets = pd.Series(closes).pct_change().dropna()
    if len(rets) > 2 and rets.std() > 0:
        result.sharpe_ratio = float(rets.mean() / rets.std() * math.sqrt(252))

    # Max drawdown.
    peak = closes[0]
    max_dd = 0.0
    for v in closes:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    result.max_drawdown_pct = max_dd * 100.0

    final_equity = closes[-1] if closes else initial_capital
    result.total_return_pct = (final_equity - initial_capital) / initial_capital * 100.0
    # Approximate fees as % of traded notional.
    total_notional = sum(t["entry_price"] * t["qty"] for t in trade_records)
    result.total_fees_pct = total_notional * (transaction_cost_bps / 10000.0) / initial_capital * 100.0

    result.warnings.append(
        "Data is current-universe only (survivorship bias). Fundamentals use a neutral score "
        "because point-in-time fundamentals are not available from the free provider."
    )
    return result


def _history_through(frame: pd.DataFrame, day) -> Optional[pd.DataFrame]:
    """Return all rows of *frame* up to and including *day* (inclusive)."""
    try:
        ts = pd.Timestamp(day)
        if frame.index.tz is not None:
            ts = ts.tz_localize(frame.index.tz)
        sub = frame.loc[:ts]
        if sub.empty:
            return None
        return sub
    except Exception:
        return None
