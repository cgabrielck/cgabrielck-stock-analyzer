#!/usr/bin/env python3
"""
Multi-month rolling evaluation — walk-forward validation for shadow mode.

Usage:
    python scripts/run_evaluation.py --orders data/shadow_orders.json --months 6 --bootstrap 1000

Design:
  - Loads shadow_orders.json
  - Splits into rolling monthly windows (e.g., 6 months)
  - Computes performance metrics for each window
  - Bootstrap resampling for confidence intervals on Sharpe/returns
  - Outputs CSV + summary table
"""
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Dict

import numpy as np
import pandas as pd

# Add parent directory to path so we can import backend modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.trading.performance.tracker import (
    evaluate_performance,
    format_report,
    load_orders,
    compute_equity_curve,
    INITIAL_CAPITAL,
    seal_stage2,
    SealCriteria,
    persist_performance_snapshot,
)
from backend.agents.risk_analyzer import calculate_risk_metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def split_orders_by_month(orders: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Group orders by year-month (YYYY-MM).
    
    Returns dict: "2026-01" → [order, order, ...]
    """
    for o in orders:
        o["filled_at_dt"] = pd.to_datetime(o.get("filled_at"))
    
    monthly = {}
    for order in orders:
        if order.get("status") != "filled":
            continue
        dt = order["filled_at_dt"]
        key = dt.strftime("%Y-%m")
        if key not in monthly:
            monthly[key] = []
        monthly[key].append(order)
    
    return monthly


def rolling_window_analysis(
    orders: List[Dict],
    window_months: int = 6,
    initial_capital: float = INITIAL_CAPITAL,
) -> pd.DataFrame:
    """
    Perform rolling-window evaluation (walk-forward).
    
    Args:
        orders:         All filled orders
        window_months:  Size of rolling window in months
        initial_capital: Starting capital for each window
    
    Returns:
        DataFrame with columns: window_start, window_end, num_trades, total_return_pct,
                                sharpe, sortino, max_dd_pct, win_rate_pct
    """
    monthly = split_orders_by_month(orders)
    sorted_months = sorted(monthly.keys())
    
    if len(sorted_months) < window_months:
        logger.warning("Insufficient data for %d-month windows (only %d months available)",
                       window_months, len(sorted_months))
        return pd.DataFrame()
    
    records = []
    for i in range(len(sorted_months) - window_months + 1):
        window = sorted_months[i:i + window_months]
        window_orders = []
        for month in window:
            window_orders.extend(monthly[month])
        
        if not window_orders:
            continue
        
        # Compute metrics for this window
        equity_df = compute_equity_curve(window_orders, initial_capital)
        if equity_df.empty:
            continue
        
        equity_series = equity_df.set_index("date")["equity"]
        risk = calculate_risk_metrics(equity_series, risk_free_rate=0.0)
        
        final_equity = float(equity_df["equity"].iloc[-1])
        total_return = (final_equity - initial_capital) / initial_capital * 100
        
        # Trade stats
        from backend.trading.performance.tracker import compute_trades_pnl
        trades = compute_trades_pnl(window_orders)
        wins = [t for t in trades if t["pnl_pct"] > 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        
        records.append({
            "window_start": window[0],
            "window_end": window[-1],
            "num_trades": len(trades),
            "total_return_pct": round(total_return, 2),
            "sharpe": risk.get("sharpe_ratio"),
            "sortino": risk.get("sortino_ratio"),
            "max_dd_pct": abs(risk.get("max_drawdown_pct", 0)),
            "win_rate_pct": round(win_rate, 1),
        })
    
    return pd.DataFrame(records)


def bootstrap_confidence_intervals(
    equity_series: pd.Series,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
) -> Dict[str, Dict[str, float]]:
    """
    Bootstrap resampling to estimate confidence intervals for Sharpe and returns.
    
    Returns dict with keys: sharpe, annual_return
    Each value is {"mean", "lower", "upper"}
    """
    returns = equity_series.pct_change().dropna()
    if len(returns) < 20:
        return {}
    
    sharpe_samples = []
    annual_return_samples = []
    
    for _ in range(n_bootstrap):
        sample = returns.sample(n=len(returns), replace=True)
        # Sharpe
        mean_ret = sample.mean()
        std_ret = sample.std(ddof=1)
        if std_ret > 0:
            sharpe = mean_ret / std_ret * np.sqrt(252)
            sharpe_samples.append(sharpe)
        # Annualized return
        cumulative = (1 + sample).prod()
        years = len(sample) / 252
        annual = (cumulative ** (1 / years) - 1) * 100 if years > 0 else 0
        annual_return_samples.append(annual)
    
    alpha = (1 - confidence) / 2
    
    result = {}
    if sharpe_samples:
        result["sharpe"] = {
            "mean": float(np.mean(sharpe_samples)),
            "lower": float(np.percentile(sharpe_samples, alpha * 100)),
            "upper": float(np.percentile(sharpe_samples, (1 - alpha) * 100)),
        }
    if annual_return_samples:
        result["annual_return_pct"] = {
            "mean": float(np.mean(annual_return_samples)),
            "lower": float(np.percentile(annual_return_samples, alpha * 100)),
            "upper": float(np.percentile(annual_return_samples, (1 - alpha) * 100)),
        }
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Multi-month shadow-mode evaluation")
    parser.add_argument("--orders", default="data/shadow_orders.json",
                        help="Path to shadow_orders.json (default: data/shadow_orders.json)")
    parser.add_argument("--months", type=int, default=6,
                        help="Rolling window size in months (default: 6)")
    parser.add_argument("--bootstrap", type=int, default=0,
                        help="Bootstrap samples for CI (0=skip, default: 0)")
    parser.add_argument("--output", default=None,
                        help="CSV output path for rolling window results (default: stdout)")
    parser.add_argument("--capital", type=float, default=INITIAL_CAPITAL,
                        help=f"Initial capital (default: {INITIAL_CAPITAL})")
    parser.add_argument("--seal", action="store_true",
                        help="Write Stage-2 seal decision to performance_shadow.json")
    parser.add_argument("--min-sharpe", type=float, default=1.0,
                        help="Seal gate: minimum Sharpe (default: 1.0)")
    parser.add_argument("--max-dd", type=float, default=20.0,
                        help="Seal gate: maximum drawdown pct (default: 20)")
    parser.add_argument("--min-months", type=int, default=1,
                        help="Seal gate: minimum calendar months of evidence (default: 1)")
    parser.add_argument("--require-alpha", action="store_true",
                        help="Seal gate: require positive alpha vs SPY")
    args = parser.parse_args()
    
    orders_path = Path(args.orders)
    if not orders_path.exists():
        logger.error("Orders file not found: %s", orders_path)
        return 1
    
    # Full-period evaluation
    logger.info("=" * 60)
    logger.info("FULL-PERIOD EVALUATION")
    logger.info("=" * 60)
    report = evaluate_performance(orders_path, initial_capital=args.capital)
    print(format_report(report))

    seal = seal_stage2(
        report,
        SealCriteria(
            min_sharpe=args.min_sharpe,
            max_drawdown_pct=args.max_dd,
            min_calendar_months=args.min_months,
            require_positive_alpha=args.require_alpha,
        ),
    )
    print("\nSTAGE-2 SEAL:", "PASS" if seal.passed else "FAIL")
    if seal.reasons:
        for reason in seal.reasons:
            print(f"  - {reason}")
    if args.seal:
        out = persist_performance_snapshot(report, seal, mode="shadow")
        logger.info("Wrote seal artifact: %s", out)
    
    # Rolling window analysis
    logger.info("\n" + "=" * 60)
    logger.info("ROLLING %d-MONTH WINDOWS", args.months)
    logger.info("=" * 60)
    
    orders = load_orders(orders_path)
    filled = [o for o in orders if o.get("status") == "filled"]
    
    if not filled:
        logger.warning("No filled orders found.")
        return 1 if not seal.passed else 0
    
    rolling_df = rolling_window_analysis(filled, window_months=args.months, initial_capital=args.capital)
    
    if rolling_df.empty:
        logger.warning("Insufficient data for rolling window analysis.")
    else:
        print(rolling_df.to_string(index=False))
        
        if args.output:
            rolling_df.to_csv(args.output, index=False)
            logger.info("Rolling window results saved to: %s", args.output)
    
    # Bootstrap CI (optional, expensive)
    if args.bootstrap > 0 and not report.equity_curve.empty:
        logger.info("\n" + "=" * 60)
        logger.info("BOOTSTRAP CONFIDENCE INTERVALS (n=%d)", args.bootstrap)
        logger.info("=" * 60)
        
        ci = bootstrap_confidence_intervals(report.equity_curve, n_bootstrap=args.bootstrap)
        
        if "sharpe" in ci:
            s = ci["sharpe"]
            print(f"Sharpe Ratio:       {s['mean']:.2f}  [{s['lower']:.2f}, {s['upper']:.2f}]")
        if "annual_return_pct" in ci:
            r = ci["annual_return_pct"]
            print(f"Annualized Return:  {r['mean']:.2f}%  [{r['lower']:.2f}%, {r['upper']:.2f}%]")
    
    return 0 if seal.passed else 3


if __name__ == "__main__":
    sys.exit(main())
