"""Run strategy bakeoff vs SPY and write summaries under backend/data/.

Usage:
  .venv/Scripts/python.exe scripts/run_strategy_bakeoff.py
  .venv/Scripts/python.exe scripts/run_strategy_bakeoff.py --baseline-only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(Path(".env"))

from backend.backtesting.strategy_backtest import run_strategy_backtest

OUT_DIR = Path("backend/data")
START = "2023-08-13"
END = "2026-08-13"


def _summary(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("trades", "equity_curve")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-only", action="store_true", help="Legacy ids + fixed_pct only")
    parser.add_argument("--limit", type=int, default=0, help="Limit universe size (0=full)")
    args = parser.parse_args()

    tickers = None
    if args.limit and args.limit > 0:
        from backend.utils.constants import STOCK_UNIVERSE

        tickers = [s["ticker"] for s in STOCK_UNIVERSE if s["ticker"] != "SPY"][: args.limit]
        print(f"Universe limited to {len(tickers)} names")

    baselines = [
        ("stable", "fixed_pct"),
        ("aggressive", "fixed_pct"),
        ("hybrid", "fixed_pct"),
    ]
    bakeoff = [
        ("trend", "risk_pct"),
        ("adaptive", "risk_pct"),
        ("breakout", "risk_pct"),
        ("stable", "risk_pct"),
    ]

    def run_one(strategy_id: str, sizing_mode: str) -> dict:
        print(f"\n=== {strategy_id} sizing={sizing_mode} {START}..{END} ===")
        result = run_strategy_backtest(
            strategy_id=strategy_id,
            start=START,
            end=END,
            tickers=tickers,
            transaction_cost_bps=10.0,
            sizing_mode=sizing_mode,
            risk_pct=0.01,
            max_positions=10,
        )
        d = result.to_dict()
        summary = _summary(d)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        out = OUT_DIR / f"strategy_backtest_{strategy_id}_{sizing_mode}_summary.json"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
        return summary

    rows = []
    for sid, mode in baselines:
        rows.append(run_one(sid, mode))
    legacy = OUT_DIR / "strategy_backtest_stable_fixed_pct_summary.json"
    desk = OUT_DIR / "strategy_backtest_stable_summary.json"
    if legacy.exists():
        desk.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")

    if not args.baseline_only:
        for sid, mode in bakeoff:
            rows.append(run_one(sid, mode))

    table = [
        {
            "strategy": r.get("strategy_id"),
            "sizing": r.get("sizing_mode"),
            "return_pct": r.get("total_return_pct"),
            "spy_pct": r.get("spy_return_pct"),
            "alpha_net": r.get("alpha_net_of_costs_pct"),
            "avg_cash_pct": r.get("avg_cash_pct"),
            "trades": r.get("num_trades"),
            "sharpe": r.get("sharpe_ratio"),
            "max_dd": r.get("max_drawdown_pct"),
        }
        for r in rows
    ]
    payload = {
        "disclaimer": (
            "12-name smoke bakeoff 2023-08..2026-08 unless --limit overridden. "
            "Do not claim alpha unless a named mode beats SPY net of costs. "
            "Default paper mode=breakout. P3 Polygon+shadow seal is still required (deferred)."
        ),
        "rows": table,
        "start": START,
        "end": END,
        "limit": args.limit or None,
    }
    bakeoff_path = OUT_DIR / "strategy_bakeoff_summary.json"
    bakeoff_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nBakeoff table → {bakeoff_path}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
