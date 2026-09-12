# Architecture Decisions

**Current decision: ADR-002** (2026-09-12). ADR-001 is kept below as history only — it is **superseded**, not deleted.

---

## ADR-002 — Two SKUs, FastAPI desk as product, keep the risk kernel

**Status:** Accepted  
**Date:** 2026-09-12  
**Decision ID:** ADR-002  
**Supersedes:** ADR-001  
**Operating plan:** [`CGAB_WORLD_CLASS_UPGRADE.md`](CGAB_WORLD_CLASS_UPGRADE.md)

### Context

The product goal is a **sellable** top-tier AI system with two functions: (1) world-class stock analysis, (2) paper-proven auto-trading that can go live after evidence. Famous GitHub projects (TradingAgents, Qlib, OpenBB, Nautilus, Freqtrade) were graded against Cgab. ADR-001’s “keep Streamlit as the product UI” no longer matches where Scan/Deep/Auto actually live (FastAPI desk) or what we will sell.

### Decision

1. **Do not greenfield-rewrite the repo.** Keep RiskEngine, mandate, kill switch, Kelly, Alpaca `BrokerAdapter`, scoring DNA, and the rule that LLMs/Telegram cannot place orders.
2. **Product UI is the FastAPI desk** (`web/static`, `backend/api/app.py`). Streamlit (`backend/app.py`) is **lab / legacy** until feature parity, then optional retire — not the commercial surface.
3. **Two SKUs, one kernel:** Research (analysis) and Paper Auto. Live is a gated add-on, not unsupervised.
4. **Steal mechanisms, not repos.** Qlib-shaped factor lab in our code; TradingAgents debate is advisory only; Nautilus/Lean may later sidecar simulation; never become a crypto/vnpy bot.
5. **Sequence truth before theatre:** worker universe + why-no-trade + scan freshness + optional research-list strategy **before** FinBERT, committee UI, or QuantStats PDF factories.
6. Paid bars (Polygon) on the seal/paper path; Yahoo is labeled fallback.

### Consequences

- Dual UI is a tax; new features land on the desk first.
- Stable mean-reversion stays a named strategy. “Buy the Scan list” is a **separate** strategy flag, not a silent replacement.
- Expanding the worker universe requires batching and rate limits (not a naive 74-ticker Yahoo burst).
- Customer live still requires Stage-2 seal + paper runbook. 24/5 paper may be demoed now.

---

## ADR-001 — Keep Research UI, Upgrade Execution & Data (superseded)

**Status:** Superseded by ADR-002  
**Date:** 2026-09-10  
**Decision ID:** ADR-001

Kept for audit. Original intent: do not rebuild from scratch; keep scoring, RiskEngine, Kelly, mandate, kill switch, Alpaca; upgrade PIT/data and seal evidence; optional Lean sidecar.

What no longer binds: **Streamlit as the primary product UI.**
