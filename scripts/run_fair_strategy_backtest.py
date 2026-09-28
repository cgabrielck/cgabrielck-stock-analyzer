"""Fair head-to-head backtest of Stable / Aggressive / Hybrid vs SPY.

Fairness protocol (locked for all strategies):
  - Same calendar window
  - Same ticker universe (STOCK_UNIVERSE, excluding SPY)
  - Same shared price snapshot (fetched once)
  - Same capital, max positions, position slice, transaction costs + slippage
  - Same next-bar-open fill model
  - Neutral fundamental scores (point-in-time fundamentals unavailable)
  - SPY buy-and-hold as the primary benchmark

research_list is excluded: it depends on a live Scan book, not historical signals.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(".env"))

from backend.backtesting.engine import fetch_price_data
from backend.backtesting.strategy_backtest import (
    BACKTEST_FETCH_WORKERS,
    STARTING_CAPITAL,
    WARMUP_DAYS,
    run_strategy_backtest,
)
from backend.trading.engine.shadow import DEFAULT_SLIPPAGE_BPS
from backend.utils.constants import STOCK_UNIVERSE

# Locked fair-test knobs — do not vary per strategy.
START = "2023-09-28"
END = "2026-09-26"
TRANSACTION_COST_BPS = 10.0
MAX_POSITIONS = 10
INITIAL_CAPITAL = STARTING_CAPITAL
STRATEGIES = ("stable", "aggressive", "hybrid")


def _annualized_return(total_return_pct: float, start: str, end: str) -> float:
    years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25, 1e-9)
    growth = 1.0 + total_return_pct / 100.0
    if growth <= 0:
        return -100.0
    return (growth ** (1.0 / years) - 1.0) * 100.0


def _buy_hold_metrics(
    series: pd.Series,
    start: str,
    end: str,
    initial_capital: float = INITIAL_CAPITAL,
    label: str = "benchmark",
) -> Dict[str, Any]:
    s = series.copy()
    if s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    s = s.sort_index()
    window = s.loc[pd.Timestamp(start) : pd.Timestamp(end)].dropna()
    if len(window) < 2:
        return {
            "strategy_id": label,
            "kind": "buy_hold",
            "total_return_pct": None,
            "annualized_return_pct": None,
            "sharpe_ratio": None,
            "max_drawdown_pct": None,
            "num_trades": 0,
            "warning": "insufficient_price_history",
        }

    start_px = float(window.iloc[0])
    end_px = float(window.iloc[-1])
    total_return_pct = (end_px / start_px - 1.0) * 100.0
    equity = (window / start_px) * initial_capital
    rets = equity.pct_change().dropna()
    sharpe = 0.0
    if len(rets) > 2 and float(rets.std()) > 0:
        sharpe = float(rets.mean() / rets.std() * math.sqrt(252))
    peak = equity.iloc[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)

    return {
        "strategy_id": label,
        "kind": "buy_hold",
        "total_return_pct": round(total_return_pct, 2),
        "annualized_return_pct": round(_annualized_return(total_return_pct, start, end), 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "win_rate_pct": None,
        "profit_factor": None,
        "num_trades": 1,
        "total_fees_pct": 0.0,
        "start_price": round(start_px, 4),
        "end_price": round(end_px, 4),
        "warnings": [],
    }


def _equal_weight_buy_hold(
    price_data: Dict[str, pd.DataFrame],
    start: str,
    end: str,
) -> Dict[str, Any]:
    """Equal-weight buy-and-hold of every ticker that has prices on both ends."""
    rets: List[float] = []
    used = 0
    for ticker, df in price_data.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        close = df["Close"].copy()
        if close.index.tz is not None:
            close.index = close.index.tz_localize(None)
        window = close.loc[pd.Timestamp(start) : pd.Timestamp(end)].dropna()
        if len(window) < 2:
            continue
        rets.append(float(window.iloc[-1] / window.iloc[0] - 1.0))
        used += 1
    if not rets:
        return {
            "strategy_id": "equal_weight_universe",
            "kind": "buy_hold",
            "total_return_pct": None,
            "warning": "no_tickers",
        }
    avg = sum(rets) / len(rets) * 100.0
    return {
        "strategy_id": "equal_weight_universe",
        "kind": "buy_hold",
        "total_return_pct": round(avg, 2),
        "annualized_return_pct": round(_annualized_return(avg, start, end), 2),
        "sharpe_ratio": None,
        "max_drawdown_pct": None,
        "win_rate_pct": None,
        "profit_factor": None,
        "num_trades": used,
        "total_fees_pct": 0.0,
        "tickers_used": used,
        "warnings": [
            "Equal-weight return is the average of individual buy-hold returns "
            "(no daily rebalancing); drawdown/Sharpe not computed."
        ],
    }


def _summarize_strategy(strategy_id: str, result) -> Dict[str, Any]:
    d = result.to_dict()
    summary = {k: v for k, v in d.items() if k not in ("trades", "equity_curve")}
    summary["strategy_id"] = strategy_id
    summary["kind"] = "strategy"
    summary["annualized_return_pct"] = round(
        _annualized_return(float(summary.get("total_return_pct") or 0.0), START, END), 2
    )
    # Compact exit-reason mix for the report.
    reasons: Dict[str, int] = {}
    for trade in d.get("trades") or []:
        reason = str(trade.get("exit_reason") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    summary["exit_reasons"] = reasons
    return summary


def main() -> None:
    tickers = [s["ticker"] for s in STOCK_UNIVERSE if s["ticker"] != "SPY"]
    fetch_start = (pd.Timestamp(START) - pd.DateOffset(days=WARMUP_DAYS + 60)).strftime("%Y-%m-%d")
    fetch_end = (pd.Timestamp(END) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"Fair backtest window: {START} .. {END}")
    print(f"Universe size: {len(tickers)} | capital={INITIAL_CAPITAL} | "
          f"cost={TRANSACTION_COST_BPS}bps + slip={DEFAULT_SLIPPAGE_BPS}bps | "
          f"max_positions={MAX_POSITIONS}")
    print("Fetching shared price snapshot (once)...")

    shared = fetch_price_data(
        tickers + ["SPY"],
        fetch_start,
        fetch_end,
        max_workers=BACKTEST_FETCH_WORKERS,
    )
    strategy_prices = {t: shared[t] for t in tickers if t in shared}
    print(f"Loaded {len(strategy_prices)}/{len(tickers)} strategy tickers; "
          f"SPY={'yes' if 'SPY' in shared else 'no'}")

    rows: List[Dict[str, Any]] = []
    for sid in STRATEGIES:
        print(f"Running {sid}...")
        result = run_strategy_backtest(
            strategy_id=sid,
            start=START,
            end=END,
            tickers=tickers,
            transaction_cost_bps=TRANSACTION_COST_BPS,
            max_positions=MAX_POSITIONS,
            initial_capital=INITIAL_CAPITAL,
            price_data=strategy_prices,
        )
        rows.append(_summarize_strategy(sid, result))
        print(
            f"  {sid}: return={rows[-1]['total_return_pct']}% "
            f"sharpe={rows[-1]['sharpe_ratio']} "
            f"dd={rows[-1]['max_drawdown_pct']}% "
            f"trades={rows[-1]['num_trades']}"
        )

    spy_metrics: Optional[Dict[str, Any]] = None
    if "SPY" in shared and "Close" in shared["SPY"].columns:
        spy_metrics = _buy_hold_metrics(shared["SPY"]["Close"], START, END, label="SPY_buy_hold")
        rows.append(spy_metrics)
        print(
            f"  SPY_buy_hold: return={spy_metrics['total_return_pct']}% "
            f"sharpe={spy_metrics['sharpe_ratio']} "
            f"dd={spy_metrics['max_drawdown_pct']}%"
        )

    ew = _equal_weight_buy_hold(strategy_prices, START, END)
    rows.append(ew)
    print(f"  equal_weight_universe: return={ew.get('total_return_pct')}%")

    # Excess vs SPY for strategy rows only.
    spy_ret = (spy_metrics or {}).get("total_return_pct")
    for row in rows:
        if row.get("kind") == "strategy" and spy_ret is not None and row.get("total_return_pct") is not None:
            row["excess_vs_spy_pct"] = round(float(row["total_return_pct"]) - float(spy_ret), 2)

    ranked = sorted(
        [r for r in rows if r.get("kind") == "strategy" and r.get("total_return_pct") is not None],
        key=lambda r: float(r["total_return_pct"]),
        reverse=True,
    )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fairness_protocol": {
            "start": START,
            "end": END,
            "universe": "STOCK_UNIVERSE excluding SPY",
            "universe_size_requested": len(tickers),
            "universe_size_loaded": len(strategy_prices),
            "initial_capital": INITIAL_CAPITAL,
            "max_positions": MAX_POSITIONS,
            "position_slice_pct": 5.0,
            "transaction_cost_bps": TRANSACTION_COST_BPS,
            "slippage_bps": DEFAULT_SLIPPAGE_BPS,
            "fill_model": "signal on close → next-bar open + slippage",
            "fundamental_scores": "neutral pass-through (70 stable/hybrid, 50 aggressive)",
            "shared_price_snapshot": True,
            "excluded": {
                "research_list": "Depends on live Scan book; not historically reproducible.",
            },
            "known_biases": [
                "Survivorship bias: current-universe constituents only (yfinance).",
                "No point-in-time fundamentals / filings lag.",
                "No walk-forward / purged CV in this head-to-head run.",
            ],
        },
        "results": rows,
        "ranking_by_total_return": [r["strategy_id"] for r in ranked],
    }

    out_json = Path("backend/data/fair_strategy_backtest_report.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_json}")

    out_md = Path("docs/FAIR_STRATEGY_BACKTEST_REPORT.md")
    out_md.write_text(_render_markdown(report), encoding="utf-8")
    print(f"Wrote {out_md}")


def _render_markdown(report: Dict[str, Any]) -> str:
    proto = report["fairness_protocol"]
    lines = [
        "# Fair Strategy Backtest Report",
        "",
        f"**Generated (UTC):** {report['generated_at_utc']}  ",
        f"**Window:** `{proto['start']}` → `{proto['end']}`  ",
        f"**Universe loaded:** {proto['universe_size_loaded']} / {proto['universe_size_requested']} tickers  ",
        "",
        "## Fairness protocol",
        "",
        "| Knob | Value |",
        "|------|-------|",
        f"| Capital | ${proto['initial_capital']:,.0f} |",
        f"| Max positions | {proto['max_positions']} |",
        f"| Position slice | {proto['position_slice_pct']}% of cash |",
        f"| Transaction cost | {proto['transaction_cost_bps']} bps |",
        f"| Slippage | {proto['slippage_bps']} bps |",
        f"| Fill model | {proto['fill_model']} |",
        f"| Fundamentals | {proto['fundamental_scores']} |",
        f"| Shared price snapshot | {proto['shared_price_snapshot']} |",
        "",
        "**Excluded:** `research_list` — " + proto["excluded"]["research_list"],
        "",
        "**Known biases:**",
        "",
    ]
    for bias in proto["known_biases"]:
        lines.append(f"- {bias}")

    lines += [
        "",
        "## Results",
        "",
        "| Strategy | Kind | Total return % | Ann. return % | Excess vs SPY % | Sharpe | Max DD % | Win rate % | Profit factor | Trades |",
        "|----------|------|----------------|---------------|-----------------|--------|----------|------------|---------------|--------|",
    ]
    for row in report["results"]:
        lines.append(
            "| {sid} | {kind} | {ret} | {ann} | {xs} | {sh} | {dd} | {wr} | {pf} | {n} |".format(
                sid=row.get("strategy_id"),
                kind=row.get("kind"),
                ret=_fmt(row.get("total_return_pct")),
                ann=_fmt(row.get("annualized_return_pct")),
                xs=_fmt(row.get("excess_vs_spy_pct")),
                sh=_fmt(row.get("sharpe_ratio")),
                dd=_fmt(row.get("max_drawdown_pct")),
                wr=_fmt(row.get("win_rate_pct")),
                pf=_fmt(row.get("profit_factor")),
                n=row.get("num_trades") if row.get("num_trades") is not None else "—",
            )
        )

    lines += [
        "",
        f"**Ranking by total return (strategies only):** {', '.join(report['ranking_by_total_return']) or 'n/a'}",
        "",
        "## Exit reason mix (strategies)",
        "",
    ]
    for row in report["results"]:
        if row.get("kind") != "strategy":
            continue
        reasons = row.get("exit_reasons") or {}
        if not reasons:
            lines.append(f"- `{row['strategy_id']}`: no trades")
            continue
        mix = ", ".join(f"{k}={v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
        lines.append(f"- `{row['strategy_id']}`: {mix}")

    lines += [
        "",
        "## How to reproduce",
        "",
        "```bash",
        "PYTHONPATH=/workspace:/workspace/backend python3 scripts/run_fair_strategy_backtest.py",
        "```",
        "",
        "Artifacts: `backend/data/fair_strategy_backtest_report.json`, `docs/FAIR_STRATEGY_BACKTEST_REPORT.md`.",
        "",
    ]
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}" if abs(value) < 1000 else f"{value:.3f}"
    return str(value)


if __name__ == "__main__":
    main()
