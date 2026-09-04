"""
Performance Tracker — multi-month validation metrics for shadow-mode testing.

Loads shadow_orders.json and computes realized P&L, Sharpe, Sortino, win rate,
max drawdown, and benchmark (SPY) comparison. Designed for the evaluation script
and the Streamlit monitoring dashboard.

Design:
  - Reuses risk_analyzer.py for metric computation
  - Computes portfolio equity curve from filled orders
  - Returns a PerformanceReport dataclass with all key metrics
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backend.agents.risk_analyzer import calculate_risk_metrics
from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

TRADING_DAYS = 252
INITIAL_CAPITAL = 100_000.0  # Default starting capital for P&L calculation


@dataclass
class PerformanceReport:
    """Container for a full performance evaluation."""
    
    # Period
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    trading_days: int = 0
    
    # P&L
    initial_capital: float = INITIAL_CAPITAL
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    
    # Risk
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    max_drawdown_pct: float = 0.0
    volatility_pct: float = 0.0
    var_95_daily_pct: float = 0.0
    
    # Trading
    num_trades: int = 0
    num_wins: int = 0
    num_losses: int = 0
    win_rate_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: Optional[float] = None  # sum(wins) / abs(sum(losses))
    
    # Benchmark
    spy_return_pct: float = 0.0
    alpha_pct: float = 0.0  # portfolio - SPY
    beta: Optional[float] = None
    
    # Equity curve (date → equity)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    
    # Raw trades (for detailed reporting)
    trades: List[Dict] = field(default_factory=list)


def load_orders(filepath: Optional[Path] = None) -> List[Dict]:
    """Load orders from shadow_orders.json or trading_orders.json."""
    if filepath is None:
        filepath = Path(DATA_DIR) / "shadow_orders.json"
    if not filepath.exists():
        return []
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        return data.get("orders", [])
    except Exception as exc:
        logger.error("Failed to load orders from %s: %s", filepath, exc)
        return []


def compute_equity_curve(orders: List[Dict], initial_capital: float = INITIAL_CAPITAL) -> pd.DataFrame:
    """
    Build daily equity curve from filled orders.
    
    Returns DataFrame with columns: date, cash, position_value, equity
    """
    filled = [o for o in orders if o.get("status") == "filled"]
    if not filled:
        return pd.DataFrame({"date": [], "cash": [], "position_value": [], "equity": []})
    
    # Sort by filled_at
    for o in filled:
        o["filled_at_dt"] = pd.to_datetime(o.get("filled_at"))
    filled = sorted(filled, key=lambda x: x["filled_at_dt"])
    
    cash = initial_capital
    positions: Dict[str, float] = {}  # symbol → quantity
    
    records = []
    for order in filled:
        dt = order["filled_at_dt"]
        symbol = order["symbol"]
        side = order["side"]
        qty = float(order["quantity"])
        price = float(order.get("filled_avg_price") or order.get("limit_price", 0))
        
        if side == "buy":
            cash -= qty * price
            positions[symbol] = positions.get(symbol, 0) + qty
        elif side == "sell":
            cash += qty * price
            positions[symbol] = positions.get(symbol, 0) - qty
            if positions[symbol] <= 0:
                positions.pop(symbol, None)
        
        # Mark-to-market: use fill price as proxy (in real tracking we'd fetch current price)
        position_value = sum(positions.get(s, 0) * price for s in positions)
        equity = cash + position_value
        
        records.append({
            "date": dt.date(),
            "cash": cash,
            "position_value": position_value,
            "equity": equity,
        })
    
    df = pd.DataFrame(records)
    # Aggregate to daily (last equity of the day)
    df = df.groupby("date").last().reset_index()
    df["date"] = pd.to_datetime(df["date"])
    return df


def compute_trades_pnl(orders: List[Dict]) -> List[Dict]:
    """
    Match buys and sells to compute per-trade P&L.
    
    Simple FIFO matching: first buy matched to first sell for each symbol.
    Returns list of dicts with symbol, entry_price, exit_price, pnl_pct.
    """
    buys: Dict[str, List[Tuple[float, float]]] = {}  # symbol → [(price, qty), ...]
    trades = []
    
    filled = [o for o in orders if o.get("status") == "filled"]
    for o in filled:
        o["filled_at_dt"] = pd.to_datetime(o.get("filled_at"))
    filled = sorted(filled, key=lambda x: x["filled_at_dt"])
    
    for order in filled:
        symbol = order["symbol"]
        side = order["side"]
        qty = float(order["quantity"])
        price = float(order.get("filled_avg_price") or order.get("limit_price", 0))
        
        if side == "buy":
            if symbol not in buys:
                buys[symbol] = []
            buys[symbol].append((price, qty))
        elif side == "sell":
            if symbol not in buys or not buys[symbol]:
                continue  # orphan sell
            entry_price, entry_qty = buys[symbol].pop(0)
            pnl_pct = (price - entry_price) / entry_price * 100 if entry_price > 0 else 0
            trades.append({
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price": price,
                "quantity": min(qty, entry_qty),
                "pnl_pct": pnl_pct,
            })
    
    return trades


def fetch_spy_returns(start_date: datetime, end_date: datetime) -> Optional[pd.Series]:
    """Fetch SPY daily returns for benchmark comparison."""
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        hist = spy.history(start=start_date, end=end_date)
        if hist.empty:
            return None
        return hist["Close"].pct_change().dropna()
    except Exception as exc:
        logger.warning("SPY fetch failed: %s", exc)
        return None


def evaluate_performance(
    orders_path: Optional[Path] = None,
    initial_capital: float = INITIAL_CAPITAL,
) -> PerformanceReport:
    """
    Full performance evaluation from shadow orders.
    
    Args:
        orders_path:     Path to shadow_orders.json (defaults to DATA_DIR/shadow_orders.json)
        initial_capital: Starting capital for equity curve calculation
    
    Returns:
        PerformanceReport with all computed metrics
    """
    orders = load_orders(orders_path)
    report = PerformanceReport(initial_capital=initial_capital)
    
    if not orders:
        logger.warning("No orders found for performance evaluation.")
        return report
    
    # Equity curve
    equity_df = compute_equity_curve(orders, initial_capital)
    if equity_df.empty:
        return report
    
    report.start_date = equity_df["date"].min().to_pydatetime()
    report.end_date = equity_df["date"].max().to_pydatetime()
    report.trading_days = len(equity_df)
    report.final_equity = float(equity_df["equity"].iloc[-1])
    report.total_return_pct = (report.final_equity - initial_capital) / initial_capital * 100
    
    if report.trading_days > 0:
        years = report.trading_days / TRADING_DAYS
        report.annualized_return_pct = ((1 + report.total_return_pct / 100) ** (1 / years) - 1) * 100
    
    # Risk metrics from equity curve
    equity_series = equity_df.set_index("date")["equity"]
    report.equity_curve = equity_series
    
    risk = calculate_risk_metrics(equity_series, risk_free_rate=0.0)
    if risk.get("available"):
        report.sharpe_ratio = risk.get("sharpe_ratio")
        report.sortino_ratio = risk.get("sortino_ratio")
        report.max_drawdown_pct = abs(risk.get("max_drawdown_pct", 0))
        report.volatility_pct = risk.get("annual_volatility_pct", 0)
        report.var_95_daily_pct = risk.get("var_95_daily_pct", 0)
    
    # Trades P&L
    trades = compute_trades_pnl(orders)
    report.trades = trades
    report.num_trades = len(trades)
    
    if trades:
        wins = [t for t in trades if t["pnl_pct"] > 0]
        losses = [t for t in trades if t["pnl_pct"] <= 0]
        report.num_wins = len(wins)
        report.num_losses = len(losses)
        report.win_rate_pct = len(wins) / len(trades) * 100
        report.avg_win_pct = float(np.mean([t["pnl_pct"] for t in wins])) if wins else 0
        report.avg_loss_pct = float(np.mean([t["pnl_pct"] for t in losses])) if losses else 0
        
        total_wins = sum(t["pnl_pct"] for t in wins)
        total_losses = abs(sum(t["pnl_pct"] for t in losses))
        if total_losses > 0:
            report.profit_factor = total_wins / total_losses
    
    # Benchmark (SPY)
    if report.start_date and report.end_date:
        spy_returns = fetch_spy_returns(report.start_date, report.end_date)
        if spy_returns is not None and len(spy_returns) > 0:
            spy_total = (1 + spy_returns).prod() - 1
            report.spy_return_pct = spy_total * 100
            report.alpha_pct = report.total_return_pct - report.spy_return_pct
            
            # Beta calculation
            port_returns = equity_series.pct_change().dropna()
            aligned = pd.concat([port_returns, spy_returns], axis=1, join="inner").dropna()
            if len(aligned) >= 20 and aligned.iloc[:, 1].var() > 0:
                report.beta = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]) / aligned.iloc[:, 1].var())
    
    return report


def format_report(report: PerformanceReport) -> str:
    """Pretty-print a PerformanceReport for CLI/logging."""
    lines = [
        "=" * 60,
        "PERFORMANCE REPORT",
        "=" * 60,
        f"Period: {report.start_date.date() if report.start_date else 'N/A'} → "
        f"{report.end_date.date() if report.end_date else 'N/A'} ({report.trading_days} days)",
        "",
        "P&L:",
        f"  Initial Capital:     ${report.initial_capital:,.0f}",
        f"  Final Equity:        ${report.final_equity:,.0f}",
        f"  Total Return:        {report.total_return_pct:+.2f}%",
        f"  Annualized Return:   {report.annualized_return_pct:+.2f}%",
        "",
        "Risk:",
        f"  Sharpe Ratio:        {report.sharpe_ratio if report.sharpe_ratio else 'N/A'}",
        f"  Sortino Ratio:       {report.sortino_ratio if report.sortino_ratio else 'N/A'}",
        f"  Max Drawdown:        {report.max_drawdown_pct:.2f}%",
        f"  Volatility (annual): {report.volatility_pct:.2f}%",
        f"  VaR 95% (daily):     {report.var_95_daily_pct:.2f}%",
        "",
        "Trading:",
        f"  Trades:              {report.num_trades}",
        f"  Win Rate:            {report.win_rate_pct:.1f}% ({report.num_wins}W / {report.num_losses}L)",
        f"  Avg Win:             {report.avg_win_pct:+.2f}%",
        f"  Avg Loss:            {report.avg_loss_pct:+.2f}%",
        f"  Profit Factor:       {report.profit_factor:.2f}" if report.profit_factor else "  Profit Factor:       N/A",
        "",
        "Benchmark (SPY):",
        f"  SPY Return:          {report.spy_return_pct:+.2f}%",
        f"  Alpha:               {report.alpha_pct:+.2f}%",
        f"  Beta:                {report.beta:.2f}" if report.beta else "  Beta:                N/A",
        "=" * 60,
    ]
    return "\n".join(lines)
