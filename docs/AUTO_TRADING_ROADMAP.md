# Auto-Trading Upgrade Roadmap

Status: **Stage 2 (Shadow Trading) Seal Tooling Complete — evidence window still time-gated**
Last updated: 2026-09-10

## Goal

Evolve ALPHA//DESK from a risk-aware equity research terminal into a reliable automated-trading system through measured stages:

```text
Research and validation (✅ Completed)
→ paper trading (✅ Completed)
→ shadow trading (✅ Tooling complete; multi-month evidence in progress)
→ small-capital, human-approved live trading (Pending — see PAPER_VALIDATION_RUNBOOK)
→ tightly controlled automation (Pending)
```

The system must not progress to live execution merely because a strategy has attractive backtest results. Each stage needs evidence, operational controls, and recovery procedures before the next one begins.

## Confirmed Technical Direction

- **Python remains the primary application language.**
- Streamlit remains the **research, configuration, review, and monitoring UI**.
- Automated execution must be an independent Python service with durable state.
- The initial execution target should be **US equities/ETFs in paper trading (Alpaca integrated)**.
- LLMs may summarize evidence but must never bypass deterministic risk gates.
- **Do not rebuild the research app from scratch** — see `docs/ARCHITECTURE_DECISION.md` (ADR-001).

## Target Architecture

```text
Streamlit research terminal (✅ Integrated Trading Dashboard)
  ├─ reads only durable system status

Signal worker      ├─> SignalProcessor (✅) ─> RiskEngine (✅) ─> OrderManager (✅)
                                                                       │
                                                                       v
                                       BrokerAdapter (✅ AlpacaBroker implemented)
                                                                       │
                                                                       v
                                                                 Brokerage API

Reconciliation worker (✅ ReconciliationService) <── orders, fills, positions
```

## Current Project Baseline

**Recently Added for Auto-Trading:**
- **Stage 0 (Research):** DCF Model (Bull/Base/Bear), Portfolio Risk Analyzer, Earnings Workflow, Comps Analyzer, Thesis/Catalyst Tracker, Backtest Auditor, and Data Provenance tracker. All tested and verified.
- **Stage 1 (Paper Trading):** `AlpacaBroker` adapter, `OrderManager` (with idempotency), `RiskEngine` (order size, concentration, exposure limits), `ReconciliationService`, and `JSONOrderStore`.
- **Stage 2 (Shadow Trading):** `ShadowTradingEngine` implemented to simulate fills without broker submission. Integrated into the Streamlit UI.

## Upgrade Stages

### Stage 0 — Strengthen Research and Evidence (✅ Completed)

Goal: make signals traceable and economically testable before execution is introduced.
- ✅ Earnings preview and post-earnings review workflow.
- ✅ Three-scenario DCF.
- ✅ Comparable-company valuation workflow.
- ✅ Investment thesis tracker.
- ✅ Catalyst calendar.
- ✅ Backtest audit (slippage, turnover, bias checks).
- ✅ Portfolio-risk and diversification analysis.

### Stage 1 — Paper Trading (✅ Completed)

Goal: execute the complete lifecycle against a broker paper account.
- ✅ `BrokerAdapter` protocol.
- ✅ `AlpacaBroker` paper implementation.
- ✅ Order-intent, order, fill, and position persistence schema (`JSONOrderStore`).
- ✅ Deterministic risk gate service (`RiskEngine`).
- ✅ Order state machine and idempotency protections (`OrderManager`).
- ✅ Account and position reconciliation worker (`ReconciliationService`).
- ✅ Operator dashboard showing paper/shadow state.

### Stage 2 — Shadow Trading (✅ Tooling Complete / Evidence Window Open)

Goal: generate and risk-approve realistic order intents without submitting them to the broker.
- ✅ `ShadowTradingEngine` implemented to intercept Risk-Approved orders and simulate fills.
- ✅ **ShadowTradingEngine 2.0**: market orders fill at next-bar open ± slippage; limit orders fill only when price crosses the limit; records `filled_avg_price`/`slippage_pct`; supports partial fills.
- ✅ UI integration for manual shadow signal injection.
- ✅ Standalone worker CLI (`python -m backend.trading.engine.worker`) with live-account guard and heartbeat.
- ✅ Strategy backtest engine (`backend/backtesting/strategy_backtest.py`) with realistic fills.
- ✅ Automated strategy signals route through `SignalProcessor` (shadow or paper).
- ✅ Multi-month evaluation + Stage-2 seal (`scripts/run_evaluation.py --seal`, `docs/STAGE2_SEAL_REPORT.md`).
- 🔄 Operator must still accumulate calendar evidence (prefer ≥ 30–90 days) before Stage 3.

### Stage 3 — Small-Capital Human-Approved Live Trading (Pending)

Gate document: [`docs/PAPER_VALIDATION_RUNBOOK.md`](PAPER_VALIDATION_RUNBOOK.md)

Checklist before any `--allow-live` session:

1. Stage-2 seal `performance_shadow.json` with `seal_passed: true` over an adequate window.
2. Paper validation daily checklist completed for ≥ 30 trading days.
3. Mandate caps tightened for small capital; kill switch rehearsal logged.
4. Durable order store (SQLite/Postgres) + broker-native brackets enabled for new entries.
5. Human sign-off table in the paper runbook completed.

Until all five items are true, Stage 3 remains **blocked**.

### Stage 4 — Narrow Automated Live Execution (Pending)

Requires Stage 3 stability, no unresolved reconciliations, and explicit expansion of mandate scope. Not authorized by this revision.

## External Projects: Intended Use
*(Unchanged from previous versions)*

## Immediate Implementation Order

1. ~~Restart OpenCode so agent-model routing loads.~~ (Done)
2. ~~Create project-local development skills for trading architecture.~~ (Done)
3. ~~Define durable execution data models and `BrokerAdapter` interface.~~ (Done)
4. ~~Implement Alpaca paper-trading adapter, order lifecycle, risk gates, and reconciliation.~~ (Done)
5. ~~Add paper-trading dashboard and exhaustive failure-mode tests.~~ (Done)
6. ~~Upgrade research workflows: earnings, DCF, comps, thesis/catalyst tracking.~~ (Done)
7. ~~Connect `SignalProcessor` to strategy outputs + Stage-2 seal tooling.~~ (Done)
8. **Current:** Run shadow/paper evidence window; follow `PAPER_VALIDATION_RUNBOOK.md` before live.
