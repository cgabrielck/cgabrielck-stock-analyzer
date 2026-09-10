# Architecture Decision: Keep Research UI, Upgrade Execution & Data

**Status:** Accepted  
**Date:** 2026-09-10  
**Decision ID:** ADR-001

## Context

ALPHA//DESK is an advanced personal US-equity research terminal with Stage-2
shadow/paper trading scaffolding. A competitive review concluded the product is
roughly **5.6–6.0 / 10** against a production auto-trading bar: strong research
and risk design, weaker PIT data, sealed Stage-2 evidence, and production ops.

The product goal is a powerful US-stock auto-trading system. Cost is not the
primary constraint; correctness and staged evidence are.

## Decision

**Do not rebuild the application from scratch.**

1. **Keep** the Streamlit research / configuration / monitoring UI
   (`backend/app.py`, Deep Picks, Scan, Trading dashboard).
2. **Keep** deterministic scoring, regime, RiskEngine, Kelly sizing, mandate,
   kill switch, and the Alpaca `BrokerAdapter` path.
3. **Upgrade** paid market data, point-in-time historical universe, Stage-2
   seal evidence, durable order ledger, broker-native protective orders, and
   24/5 ops notifications.
4. **Optional later:** attach Lean / QuantConnect as an external backtest or
   execution host while ALPHA//DESK remains the research and approval surface.

## Consequences

- Stage progression remains: shadow → multi-month seal → paper runbook →
  small-capital human-approved live (`--allow-live`) → narrow automation.
- LLMs and sentiment may explain or soft-adjust confidence; they must never
  bypass RiskEngine, mandate, or kill switch.
- Yahoo remains a fallback; live and seal paths prefer Polygon (or equivalent
  paid) equity bars when configured.
- JSON order stores remain supported for tests; durable SQLite (and optional
  Postgres URL) is the production default for the worker.
