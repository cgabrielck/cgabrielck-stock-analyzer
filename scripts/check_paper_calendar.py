#!/usr/bin/env python3
"""Paper/shadow calendar checklist for named breakout (main line).

Does not start live trading. Prints readiness for P3 seal clock.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.utils.constants import DATA_DIR


def main() -> int:
    ai = Path(DATA_DIR) / "ai_mode.json"
    hb = Path(DATA_DIR) / "worker_heartbeat.json"
    paper = Path(DATA_DIR) / "performance_paper.json"
    print("=== Paper/shadow calendar (named breakout) ===")
    if ai.exists():
        cfg = json.loads(ai.read_text(encoding="utf-8"))
        strat = cfg.get("strategy")
        print(f"ai_mode.strategy = {strat}  (want: breakout; not silent research_list)")
        if str(strat).lower() in ("aggressive",):
            print("  note: aggressive is legacy alias of breakout")
        if str(strat).lower() == "research_list":
            print("  WARN: research_list is operator-chosen Scan top-N — not default DNA")
    else:
        print("ai_mode.json missing")

    if hb.exists():
        h = json.loads(hb.read_text(encoding="utf-8"))
        print(
            f"heartbeat: strategy={h.get('strategy')} mode={h.get('mode')} "
            f"running={h.get('running')} skip={h.get('skip_counts')} "
            f"paper_vs_spy={h.get('paper_vs_spy')} polygon={h.get('polygon_configured')} "
            f"ohlcv_vendor={h.get('ohlcv_vendor')} calendar={h.get('calendar')}"
        )
        want = str((json.loads(ai.read_text(encoding="utf-8")).get("strategy") if ai.exists() else "breakout") or "breakout").lower()
        got = str(h.get("strategy") or "").lower()
        if got and want and got not in (want, "aggressive" if want == "breakout" else want):
            print(f"  WARN: heartbeat strategy={got} != ai_mode={want} — restart worker after desk save")
        if got == "aggressive" and want == "breakout":
            print("  note: heartbeat aggressive ≡ breakout alias; prefer restart with --strategy breakout")
    else:
        print("worker_heartbeat.json missing — start: desk AI Mode → breakout → Start")
        print("  or: python -m backend.trading.engine.worker --strategy breakout --mode shadow")

    if paper.exists():
        p = json.loads(paper.read_text(encoding="utf-8"))
        print(
            f"performance_paper: trades={(p.get('metrics') or {}).get('num_trades')} "
            f"spy={(p.get('benchmark') or {}).get('spy_return_pct')} "
            f"seal={p.get('seal_passed')}"
        )
    else:
        print("performance_paper.json missing — refresh /api/performance?refresh=1 after fills")

    print("Seal gate: ≥30 calendar days (prefer 90) then scripts/run_evaluation.py --seal")

    polygon_configured = False
    try:
        from backend.agents.polygon_equity import is_configured

        polygon_configured = bool(is_configured())
    except Exception:
        polygon_configured = False
    print(f"polygon_configured={polygon_configured}")
    if polygon_configured:
        print("  OHLCV primary=polygon; Yahoo is labeled fallback")
    else:
        print("  OHLCV primary=yahoo (no real POLYGON_API_KEY — customer narrative stays Yahoo fallback, not live)")

    try:
        from backend.utils.us_equity_calendar import status_payload

        cal = status_payload()
        print(
            f"calendar={cal.get('calendar')} ({cal.get('label')}) "
            f"session_date={cal.get('session_date')} "
            f"trading_day={cal.get('is_trading_day')} "
            f"rth={cal.get('in_regular_session')} "
            f"next_open={cal.get('next_rth_open')}"
        )
    except Exception as exc:
        print(f"calendar=us_equity (status unavailable: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
