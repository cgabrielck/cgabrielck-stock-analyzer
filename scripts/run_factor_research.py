#!/usr/bin/env python3
"""Wave 4 factor IC/IR research report — never places orders.

Usage:
  .venv/Scripts/python.exe scripts/run_factor_research.py
  .venv/Scripts/python.exe scripts/run_factor_research.py --fixture
  .venv/Scripts/python.exe scripts/run_factor_research.py --panel tests/backtesting/fixtures/factor_panel.json
  .venv/Scripts/python.exe scripts/run_factor_research.py --out-dir backend/data

Writes dated performance_research.json (Sharpe, max DD, turnover, SPY excess).
If factors do not beat SPY net of costs, SKU B stays research-list automation, not alpha.
Does not change the default paper strategy.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Write performance_research.json (research only)")
    parser.add_argument("--fixture", action="store_true", default=True, help="Use built-in offline panel (default)")
    parser.add_argument("--panel", type=Path, default=None, help="JSON panel with rows[{date,ticker,factors,forward_return,spy_forward_return}]")
    parser.add_argument("--out-dir", type=Path, default=None, help="Directory for performance_research.json")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--cost-bps", type=float, default=10.0)
    args = parser.parse_args()

    from backtesting.factor_lab import PLACES_ORDERS, run_fixture_research

    if PLACES_ORDERS:
        raise SystemExit("factor lab must never place orders")

    report, paths = run_fixture_research(
        data_dir=args.out_dir,
        top_n=args.top_n,
        cost_bps=args.cost_bps,
        panel_path=args.panel,
    )
    print(json.dumps({k: report[k] for k in (
        "generated_at", "as_of", "source", "headline", "sku_b_narrative",
        "sku_b_positioning", "alpha_claimed", "places_orders",
        "default_paper_strategy_unchanged", "primary_factor",
    ) if k in report}, indent=2, ensure_ascii=False))
    for path in paths:
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
