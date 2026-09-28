#!/usr/bin/env python3
"""Run lab A/B metrics (schema / sentiment / advisory / tear). Not SPY bakeoff."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Exercise lab paths offline (no LLM)")
    parser.add_argument("--enable-lab-flags", action="store_true", help="Set LAB_* flags for this process")
    args = parser.parse_args()

    if args.enable_lab_flags:
        os.environ.setdefault("LAB_INSTRUCTOR", "1")
        os.environ.setdefault("LAB_FINBERT", "0")  # heavy; keep off unless transformers installed
        os.environ.setdefault("LAB_QUANTSTATS", "0")
        os.environ.setdefault("LAB_ADVISORY", "1")

    if args.demo:
        from backend.lab.instructor_parse import parse_scan_llm_text
        from backend.lab.finbert_sentiment import classify_news_sentiment
        from backend.lab.advisory_debate import run_advisory_debate
        from backend.lab.quantstats_tear import build_tear_sheet

        # Force instructor flag for schema demo if requested
        if args.enable_lab_flags:
            os.environ["LAB_INSTRUCTOR"] = "1"
        good = parse_scan_llm_text(
            '{"final_score": 72, "reasoning": "ok", "technical_summary": "up", "key_signal": "bullish"}'
        )
        bad = parse_scan_llm_text("{not-json")
        classify_news_sentiment("Company beats earnings estimates", "Strong guidance")
        adv = run_advisory_debate(
            "AAPL",
            fund_score=80,
            top_n_before=["MSFT", "NVDA"],
            context="lab demo",
        )
        tear = build_tear_sheet(
            equity_curve=[
                {"date": "2026-01-01", "equity": 100000},
                {"date": "2026-01-02", "equity": 100500},
                {"date": "2026-01-03", "equity": 101000},
                {"date": "2026-01-06", "equity": 100800},
            ]
        )
        print("demo_scan_ok", good.get("lab_schema_ok"), good.get("key_signal"))
        print("demo_scan_bad", bad.get("lab_schema_ok"))
        print("demo_advisory_rewrite", adv.get("top_n_rewritten"), adv.get("places_order"))
        print("demo_tear", tear.get("complete"), tear.get("engine"))

    from backend.lab.ab_metrics import write_ab_report

    path = write_ab_report()
    report = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
