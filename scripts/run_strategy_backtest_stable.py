"""Run realistic stable-strategy backtest and print a compact summary."""
from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(".env"))

from backend.backtesting.strategy_backtest import run_strategy_backtest

START = "2023-08-13"
END = "2026-08-13"

print(f"Running stable strategy backtest {START} .. {END} ...")
result = run_strategy_backtest(
    strategy_id="stable",
    start=START,
    end=END,
    transaction_cost_bps=10.0,  # + 5 bps slippage in fill model
)
d = result.to_dict()
# Drop bulky series from console summary
summary = {k: v for k, v in d.items() if k not in ("trades", "equity_curve")}
print(json.dumps(summary, indent=2, ensure_ascii=False))

out = Path("backend/data/strategy_backtest_stable_summary.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(f"Wrote {out}")

# Sample last few trades if any
trades = d.get("trades") or []
print(f"sample_trades={min(5, len(trades))}")
for t in trades[-5:]:
    print(
        {
            "symbol": t.get("symbol"),
            "side": t.get("side") or t.get("action"),
            "pnl_pct": t.get("pnl_pct") or t.get("return_pct"),
            "entry": t.get("entry_date") or t.get("entry"),
            "exit": t.get("exit_date") or t.get("exit"),
        }
    )
