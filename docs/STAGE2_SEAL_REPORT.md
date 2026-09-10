# Stage 2 Seal Report Template

Last updated: 2026-09-10

Use this template after a multi-month shadow (or paper) run. Generate metrics with:

```bash
PYTHONPATH=backend:. python3 scripts/run_evaluation.py \
  --orders backend/data/shadow_orders.json \
  --months 6 --seal --min-sharpe 1.0 --max-dd 20 --min-months 3
```

Artifact written: `backend/data/performance_shadow.json`

## Run metadata

| Field | Value |
|-------|-------|
| Mode | shadow / paper |
| Strategy | stable / aggressive / hybrid |
| Start date | |
| End date | |
| Universe | |
| Data providers | Polygon / Yahoo fallback |
| historical_universe.json present | yes / no |
| Operator | |

## Seal gates (must all pass)

| Gate | Threshold | Actual | Pass? |
|------|-----------|--------|-------|
| Calendar months | ≥ 3 (preferred) / ≥ 1 (minimum tooling) | | |
| Trading days | ≥ 20 | | |
| Closed trades | ≥ 5 | | |
| Sharpe | ≥ 1.0 | | |
| Max drawdown | ≤ 20% | | |
| Alpha vs SPY | optional (`--require-alpha`) | | |

## Benchmark

| Metric | Portfolio | SPY |
|--------|-----------|-----|
| Total return % | | |
| Alpha % | | |
| Beta | | |

## Decision

- [ ] **PASS** — promote to Stage 3 paper validation runbook only (still not unsupervised live)
- [ ] **FAIL** — remain in Stage 2; list remediation below

### Failure remediation

1.
2.

## Disclaimer

Shadow fills and Yahoo/survivorship-limited backtests are **not** live P&L.
Do not enable `--allow-live` until `docs/PAPER_VALIDATION_RUNBOOK.md` is completed
and a human signs the Stage 3 checklist in `docs/AUTO_TRADING_ROADMAP.md`.
