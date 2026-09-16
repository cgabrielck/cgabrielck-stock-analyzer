"""
Strategy backtesting engine — validates Stable/Aggressive/Hybrid/Trend/Adaptive
against historical OHLCV data with realistic fills and SPY benchmark.

Design:
  - Daily bars per ticker (warmup window prepended so indicators are valid).
  - For each trading day after warmup:
      * Check exits on open positions first (stop / take-profit / signal reversal).
      * Then scan the universe for entry signals via StrategyBase.generate_signal().
  - Fills use next-bar open + slippage; costs applied on entry and exit.
  - Sizing: fixed cash slice (legacy) or risk-based (Van Tharp 1R).
  - Reports SPY buy-and-hold, alpha gross/net of stated cost, cash drag.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from backend.backtesting.engine import fetch_price_data
from backend.trading.engine.shadow import DEFAULT_SLIPPAGE_BPS
from backend.trading.strategies.registry import get_strategy
from backend.trading.strategies.stage2 import size_shares_by_risk
from backend.utils.constants import STOCK_UNIVERSE

WARMUP_DAYS = 250
MAX_POSITIONS = 10
STARTING_CAPITAL = 100_000.0
BACKTEST_FETCH_WORKERS = 12
ASSUMED_ROUND_TRIP_BPS = 10.0


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
        self.spy_return_pct: Optional[float] = None
        self.alpha_gross_pct: Optional[float] = None
        self.alpha_net_of_costs_pct: Optional[float] = None
        self.avg_cash_pct: Optional[float] = None
        self.avg_invested_pct: Optional[float] = None
        self.turnover_pct: Optional[float] = None
        self.sizing_mode: str = "fixed_pct"
        self.strategy_id: str = ""
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
            "spy_return_pct": None if self.spy_return_pct is None else round(self.spy_return_pct, 2),
            "alpha_gross_pct": None if self.alpha_gross_pct is None else round(self.alpha_gross_pct, 2),
            "alpha_net_of_costs_pct": None
            if self.alpha_net_of_costs_pct is None
            else round(self.alpha_net_of_costs_pct, 2),
            "avg_cash_pct": None if self.avg_cash_pct is None else round(self.avg_cash_pct, 2),
            "avg_invested_pct": None if self.avg_invested_pct is None else round(self.avg_invested_pct, 2),
            "turnover_pct": None if self.turnover_pct is None else round(self.turnover_pct, 2),
            "sizing_mode": self.sizing_mode,
            "strategy_id": self.strategy_id,
            "warnings": self.warnings,
        }


def _neutral_fund_score(strategy_id: str) -> float:
    """Neutral fund for backtests without PIT scores — labeled as bias in warnings."""
    sid = (strategy_id or "").lower()
    if sid in ("stable", "reversion", "research_list"):
        return 70.0
    if sid in ("trend", "adaptive", "hybrid"):
        return 60.0
    return 55.0  # aggressive / breakout


def run_strategy_backtest(
    strategy_id: str = "stable",
    start: Optional[str] = None,
    end: Optional[str] = None,
    tickers: Optional[List[str]] = None,
    transaction_cost_bps: float = 10.0,
    max_positions: int = MAX_POSITIONS,
    initial_capital: float = STARTING_CAPITAL,
    max_workers: int = BACKTEST_FETCH_WORKERS,
    strategy_params: Optional[Dict[str, Any]] = None,
    sizing_mode: str = "risk_pct",
    risk_pct: float = 0.01,
    max_position_pct: float = 0.25,
    fixed_cash_pct: float = 0.05,
) -> StrategyBacktestResult:
    """Run a daily-frequency backtest of a single strategy over *start*..*end*.

    sizing_mode:
      - ``fixed_pct``: legacy cash * fixed_cash_pct per entry (old Stable runs).
      - ``risk_pct``: Van Tharp shares = equity*risk_pct / |entry-stop|.
    """
    strategy = get_strategy(strategy_id, **(strategy_params or {}))
    result = StrategyBacktestResult()
    result.sizing_mode = sizing_mode
    result.strategy_id = strategy_id

    if tickers is None:
        tickers = [s["ticker"] for s in STOCK_UNIVERSE]
    tickers = [t for t in tickers if t != "SPY"]

    end_date = end or pd.Timestamp.now().strftime("%Y-%m-%d")
    if start is None:
        start = (pd.Timestamp(end_date) - pd.DateOffset(years=3)).strftime("%Y-%m-%d")

    fetch_start = (pd.Timestamp(start) - pd.DateOffset(days=WARMUP_DAYS + 60)).strftime("%Y-%m-%d")
    fetch_end = (pd.Timestamp(end_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    fetch_list = list(tickers) + ["SPY"]
    price_data = fetch_price_data(fetch_list, fetch_start, fetch_end, max_workers=max_workers)

    missing = [t for t in tickers if t not in price_data]
    if missing:
        result.warnings.append(f"No price data for: {', '.join(missing[:8])}")

    aligned: Dict[str, pd.DataFrame] = {}
    for ticker, df in price_data.items():
        if df is None or df.empty:
            continue
        d = df.copy()
        if d.index.tz is not None:
            d.index = d.index.tz_localize(None)
        cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in d.columns]
        d = d[cols].dropna()
        d = d[~d.index.duplicated(keep="last")]
        aligned[ticker] = d

    spy_frame = aligned.pop("SPY", None)
    spy_close = spy_frame["Close"] if spy_frame is not None else None
    if hasattr(strategy, "set_spy_close"):
        strategy.set_spy_close(spy_close)
    elif hasattr(strategy, "spy_close"):
        strategy.spy_close = spy_close

    if not aligned:
        result.warnings.append("No usable price data at all.")
        return result

    try:
        from backend.utils.us_equity_calendar import (
            equity_session_days,
            filter_us_equity_daily_bars,
        )
    except ImportError:
        from utils.us_equity_calendar import (  # type: ignore
            equity_session_days,
            filter_us_equity_daily_bars,
        )

    for ticker, frame in list(aligned.items()):
        aligned[ticker] = filter_us_equity_daily_bars(frame)
    aligned = {t: f for t, f in aligned.items() if f is not None and not f.empty}
    if spy_frame is not None:
        spy_frame = filter_us_equity_daily_bars(spy_frame)
        spy_close = spy_frame["Close"] if spy_frame is not None and not spy_frame.empty else spy_close
        if hasattr(strategy, "set_spy_close"):
            strategy.set_spy_close(spy_close)
        elif hasattr(strategy, "spy_close"):
            strategy.spy_close = spy_close

    all_dates = equity_session_days(
        aligned,
        start=start,
        end=end_date,
        spy=spy_frame,
    )
    if len(all_dates) < 30:
        result.warnings.append("Too few trading dates for a meaningful backtest.")
        return result

    positions: Dict[str, Dict[str, Any]] = {}
    pending_entries: Dict[str, Any] = {}
    cash = initial_capital
    trade_records: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    cash_pcts: List[float] = []
    invested_pcts: List[float] = []

    slippage = DEFAULT_SLIPPAGE_BPS / 10000.0
    time_stop_days = getattr(strategy, "MAX_HOLD_DAYS", None)
    fund_score = _neutral_fund_score(strategy_id)

    for day_idx, day in enumerate(all_dates):
        if hasattr(strategy, "set_asof"):
            strategy.set_asof(day)

        # 0. Fill entries signalled on the previous bar at today's open + slippage.
        for ticker in list(pending_entries.keys()):
            signal = pending_entries[ticker]
            if ticker not in aligned:
                del pending_entries[ticker]
                continue
            try:
                row = aligned[ticker].loc[day]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[-1]
            except KeyError:
                continue
            if len(positions) >= max_positions or ticker in positions:
                del pending_entries[ticker]
                continue
            fill_price = float(row["Open"]) * (1 + slippage)
            if fill_price <= 0:
                del pending_entries[ticker]
                continue

            # Mark-to-market equity for risk sizing.
            equity_now = cash
            for t_held, p_held in positions.items():
                if t_held in aligned:
                    try:
                        r = aligned[t_held].loc[day]
                        if isinstance(r, pd.DataFrame):
                            r = r.iloc[-1]
                        equity_now += p_held["qty"] * float(r["Close"])
                    except KeyError:
                        equity_now += p_held["qty"] * p_held["entry_price"]
                else:
                    equity_now += p_held["qty"] * p_held["entry_price"]

            stop = float(signal.stop_loss_price or fill_price * 0.95)
            if sizing_mode == "fixed_pct":
                alloc = cash * fixed_cash_pct
                qty = max(1, int(alloc / fill_price))
                cost = qty * fill_price
                if cost > cash:
                    qty = max(1, int(cash / fill_price))
                    cost = qty * fill_price
            else:
                qty = size_shares_by_risk(
                    equity=equity_now,
                    cash=cash,
                    fill_price=fill_price,
                    stop_price=stop,
                    risk_pct=risk_pct,
                    max_position_pct=max_position_pct,
                )
                cost = qty * fill_price

            if qty <= 0 or cost <= 0 or cost > cash:
                del pending_entries[ticker]
                continue
            cash -= cost * (1 + transaction_cost_bps / 10000.0)
            positions[ticker] = {
                "entry_price": fill_price,
                "qty": qty,
                "stop": signal.stop_loss_price,
                "target": signal.take_profit_price,
                "entry_date": day.strftime("%Y-%m-%d"),
                "entry_day_idx": day_idx,
                "meta": dict(signal.meta or {}),
                "reason": signal.reason,
                "fund_score": fund_score,
                "entry_kind": (signal.meta or {}).get("entry_kind") or signal.strategy_id,
            }
            del pending_entries[ticker]

        # 1. Exits
        for ticker in list(positions.keys()):
            if ticker not in aligned:
                continue
            bar = aligned[ticker]
            try:
                row = bar.loc[day]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[-1]
            except KeyError:
                continue

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

            if exit_reason is None and time_stop_days:
                held_days = day_idx - pos.get("entry_day_idx", day_idx)
                if held_days >= int(time_stop_days):
                    exit_reason = "time_stop"
                    exit_price = close

            if exit_reason:
                proceeds = pos["qty"] * exit_price * (1 - transaction_cost_bps / 10000.0)
                cash += proceeds
                pnl = (exit_price - pos["entry_price"]) * pos["qty"]
                pnl_pct = pnl / (pos["entry_price"] * pos["qty"]) * 100.0 if pos["entry_price"] else 0.0
                meta = pos.get("meta") or {}
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
                    "entry_kind": pos.get("entry_kind"),
                    "reason": pos.get("reason"),
                    "fund_score": pos.get("fund_score"),
                    "rs_role": meta.get("rs_role"),
                    "rs_12w": meta.get("rs_12w"),
                    "rs_126": meta.get("rs_126"),
                    "rs_252": meta.get("rs_252"),
                    "macd_hist": meta.get("macd_hist"),
                })
                del positions[ticker]

        # 2. Entries
        committed = len(positions) + len(pending_entries)
        if committed < max_positions and cash > 500:
            current_pos_list = [{"symbol": t, "quantity": p["qty"]} for t, p in positions.items()]
            current_pos_list += [{"symbol": t, "quantity": 0} for t in pending_entries]
            for ticker in sorted(aligned.keys()):
                if committed >= max_positions:
                    break
                if ticker in positions or ticker in pending_entries:
                    continue
                hist = _history_through(aligned[ticker], day)
                if hist is None or len(hist) < 30:
                    continue
                signal = strategy.generate_signal(
                    ticker=ticker,
                    df=hist,
                    fundamental_score=fund_score,
                    llm_signal=None,
                    current_positions=current_pos_list,
                )
                if signal is None:
                    continue
                pending_entries[ticker] = signal
                current_pos_list.append({"symbol": ticker, "quantity": 0})
                committed += 1

        # 3. Mark-to-market + cash drag
        invested = 0.0
        for ticker, pos in positions.items():
            if ticker in aligned:
                try:
                    row = aligned[ticker].loc[day]
                    if isinstance(row, pd.DataFrame):
                        row = row.iloc[-1]
                    invested += pos["qty"] * float(row["Close"])
                except KeyError:
                    invested += pos["qty"] * pos["entry_price"]
            else:
                invested += pos["qty"] * pos["entry_price"]
        equity = cash + invested
        cash_pct = (cash / equity * 100.0) if equity > 0 else 100.0
        inv_pct = (invested / equity * 100.0) if equity > 0 else 0.0
        cash_pcts.append(cash_pct)
        invested_pcts.append(inv_pct)
        equity_curve.append({
            "date": day.strftime("%Y-%m-%d"),
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "cash_pct": round(cash_pct, 2),
            "invested_pct": round(inv_pct, 2),
        })

    # Force-close remaining
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
            "entry_kind": pos.get("entry_kind"),
        })

    result.trades = trade_records
    result.equity_curve = equity_curve
    result.num_trades = len(trade_records)
    if cash_pcts:
        result.avg_cash_pct = float(np.mean(cash_pcts))
        result.avg_invested_pct = float(np.mean(invested_pcts))

    closes = [e["equity"] for e in equity_curve]
    if closes:
        final_equity = closes[-1]
        result.total_return_pct = (final_equity - initial_capital) / initial_capital * 100.0
        rets = pd.Series(closes).pct_change().dropna()
        if len(rets) > 2 and rets.std() > 0:
            result.sharpe_ratio = float(rets.mean() / rets.std() * math.sqrt(252))
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

    # SPY buy-and-hold over the same window
    if spy_close is not None and equity_curve:
        try:
            first = pd.Timestamp(equity_curve[0]["date"])
            last = pd.Timestamp(equity_curve[-1]["date"])
            spy_sub = spy_close.loc[(spy_close.index >= first) & (spy_close.index <= last)].dropna()
            if len(spy_sub) >= 2:
                result.spy_return_pct = float(spy_sub.iloc[-1] / spy_sub.iloc[0] - 1.0) * 100.0
                result.alpha_gross_pct = result.total_return_pct - result.spy_return_pct
        except Exception:
            pass

    if trade_records:
        wins = [t for t in trade_records if t["pnl"] > 0]
        losses = [t for t in trade_records if t["pnl"] < 0]
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = -sum(t["pnl"] for t in losses)
        result.win_rate_pct = len(wins) / len(trade_records) * 100.0
        result.profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
        total_notional = sum(t["entry_price"] * t["qty"] for t in trade_records)
        result.total_fees_pct = total_notional * (transaction_cost_bps / 10000.0) / initial_capital * 100.0
        # Turnover ≈ traded notional / capital (entries only once each)
        result.turnover_pct = total_notional / initial_capital * 100.0
        # Net alpha: subtract assumed round-trip cost × turnover (same honesty as paper_performance)
        if result.alpha_gross_pct is not None and result.turnover_pct is not None:
            cost_drag = (ASSUMED_ROUND_TRIP_BPS / 10000.0) * (result.turnover_pct / 100.0) * 100.0
            result.alpha_net_of_costs_pct = result.alpha_gross_pct - cost_drag
    else:
        result.warnings.append("No trades generated. Check strategy parameters and data.")
        if result.spy_return_pct is not None:
            result.alpha_gross_pct = result.total_return_pct - result.spy_return_pct
            result.alpha_net_of_costs_pct = result.alpha_gross_pct

    result.warnings.append(
        "Data is current-universe only (survivorship bias). Fundamentals use a neutral score "
        f"({fund_score}) because point-in-time fundamentals are not available from the free provider."
    )
    if sizing_mode == "fixed_pct":
        result.warnings.append(
            f"Sizing=fixed_pct ({fixed_cash_pct*100:.0f}% cash/entry, max {max_positions} positions) "
            "→ large cash drag vs SPY is expected."
        )
    else:
        result.warnings.append(
            f"Sizing=risk_pct ({risk_pct*100:.1f}% equity per 1R, cap {max_position_pct*100:.0f}%/name)."
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
