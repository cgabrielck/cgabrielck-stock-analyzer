# Auto-Trading Upgrade Roadmap

Status: **Stage 2 (Shadow Trading) In Progress**
Last updated: 2026-08-06

## Goal

Evolve ALPHA//DESK from a risk-aware equity research terminal into a reliable automated-trading system through measured stages:

```text
Research and validation (✅ Completed)
→ paper trading (✅ Completed)
→ shadow trading (🔄 In Progress)
→ small-capital, human-approved live trading (Pending)
→ tightly controlled automation (Pending)
```

The system must not progress to live execution merely because a strategy has attractive backtest results. Each stage needs evidence, operational controls, and recovery procedures before the next one begins.

## Confirmed Technical Direction

- **Python remains the primary application language.**
- Streamlit remains the **research, configuration, review, and monitoring UI**.
- Automated execution must be an independent Python service with durable state.
- The initial execution target should be **US equities/ETFs in paper trading (Alpaca integrated)**.
- LLMs may summarize evidence but must never bypass deterministic risk gates.

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

### Stage 2 — Shadow Trading (🔄 In Progress)

Goal: generate and risk-approve realistic order intents without submitting them to the broker.
- ✅ `ShadowTradingEngine` implemented to intercept Risk-Approved orders and simulate fills.
- ✅ UI integration for manual shadow signal injection.
- 🔄 (Pending) Connect automated strategy signals to the `SignalProcessor` running in shadow mode.
- 🔄 (Pending) Multi-month evaluation window.

### Stage 3 — Small-Capital Human-Approved Live Trading (Pending)

### Stage 4 — Narrow Automated Live Execution (Pending)

## External Projects: Intended Use
*(Unchanged from previous versions)*

## Immediate Implementation Order

1. ~~Restart OpenCode so agent-model routing loads.~~ (Done)
2. ~~Create project-local development skills for trading architecture.~~ (Done)
3. ~~Define durable execution data models and `BrokerAdapter` interface.~~ (Done)
4. ~~Implement Alpaca paper-trading adapter, order lifecycle, risk gates, and reconciliation.~~ (Done)
5. ~~Add paper-trading dashboard and exhaustive failure-mode tests.~~ (Done)
6. ~~Upgrade research workflows: earnings, DCF, comps, thesis/catalyst tracking.~~ (Done)
7. **Current:** Run shadow trading by connecting the `SignalProcessor` to actual strategy outputs.
