# Architecture Decisions

**Current decision: ADR-003 + Addendum A (Accepted, 2026-10-21).**  
**ADR-002 is VOID.** ADR-001 remains history only.

This file is the **index**. Canonical ADR-003 text lives in its own file. This file does **not** change live RiskEngine / Kelly / mandate / kill settings or trading runtime code.

---

## Index (binding order, highest first)

1. **[`ADR-003-live-ai-trading-desk.md`](ADR-003-live-ai-trading-desk.md)** — **Accepted** by Gabriel (chat) 2026-10-21, including **Addendum A** (Proposal-governed self-upgrade: Learn → Proposal → Approve → Merge; L1 may auto; L2 only after Approve).
2. **[`MODULAR_ARCHITECTURE.md`](MODULAR_ARCHITECTURE.md)** — M1–M13, dual-clock H1–H3, L1/L2, `HarnessProposal`, Intent v0.
3. **[`AUDIT_REMEDIATION_PLAN_2026-10-21.md`](AUDIT_REMEDIATION_PLAN_2026-10-21.md)** — **Confirmed by Gabriel 2026-10-21** (Hong Kong time). Next-doc work under ADR-003. **Confirmed P0 order:** user-visible journal / provenance first; IntentStore (H1/H3 split) after.
   - **Draft** (pending Owner/PM; not yet Accepted): [`BOUNDED_INTELLIGENCE_ACCEPTANCE.md`](BOUNDED_INTELLIGENCE_ACCEPTANCE.md) — AUDIT item 5 / plan-14 bounded-intelligence acceptance one-pager.
   - **Draft** (pending Owner/PM; not yet Accepted; does not expand 2026-10-21 Confirmed scope): [`ACCOUNT_MANAGEMENT_DESIGN.md`](ACCOUNT_MANAGEMENT_DESIGN.md) — plan-15 account-management design page (Desk vs worker). Issue #10 stays OPEN.
   - **Draft** (pending Owner/PM; not yet Accepted; does not expand 2026-10-21 Confirmed scope): [`LAB_PRODUCT_M7_BINDING.md`](LAB_PRODUCT_M7_BINDING.md) — plan-16 lab≠product M7 binding (`lab/advisory_debate` must not wire to `OrderManager`). Issue #10 stays OPEN.
4. Older roadmaps — **frozen / superseded**, not authority:
   `AUTO_TRADING_ROADMAP`, `NEXT_PHASE_PLAN`, `QUALITY_ROADMAP`, `PAPER_VALIDATION_RUNBOOK`, `PROGRESS_LOG`, `CGAB_WORLD_CLASS_UPGRADE`.

Visual map: [`boomboom-modular-architecture.html`](boomboom-modular-architecture.html)

**ADR-002 is VOID** (owner decision 2026-10-21). Do not cite it as authoritative. Overlap with ADR-003 engineering preference (evolve `backend/trading/`, Desk = product) is coincidence, not inheritance.

Product surface under ADR-003: **Desk** = FastAPI + `web/static`. **Streamlit** = lab / legacy. Product **M7** is AI advisory in the modular map; `backend/lab/advisory_debate.py` is **not** M7 and must not wire to `OrderManager`.

---

## ADR-002 — Two SKUs, FastAPI desk as product, keep the risk kernel (VOID)

**Status:** VOID (2026-10-21). Replaced by ADR-003. Kept for audit; do not cite as binding.  
**Date:** 2026-09-12  
**Decision ID:** ADR-002  
**Supersedes:** ADR-001  
**Operating plan at the time:** [`CGAB_WORLD_CLASS_UPGRADE.md`](CGAB_WORLD_CLASS_UPGRADE.md) (that plan is now frozen)

### Context

The product goal is a **sellable** top-tier AI system with two functions: (1) world-class stock analysis, (2) paper-proven auto-trading that can go live after evidence. Famous GitHub projects (TradingAgents, Qlib, OpenBB, Nautilus, Freqtrade) were graded against Cgab. ADR-001’s “keep Streamlit as the product UI” no longer matches where Scan/Deep/Auto actually live (FastAPI desk) or what we will sell.

### Decision (historical — VOID)

1. **Do not greenfield-rewrite the repo.** Keep RiskEngine, mandate, kill switch, Kelly, Alpaca `BrokerAdapter`, scoring DNA, and the rule that LLMs/Telegram cannot place orders.
2. **Product UI is the FastAPI desk** (`web/static`, `backend/api/app.py`). Streamlit (`backend/app.py`) is **lab / legacy** until feature parity, then optional retire — not the commercial surface.
3. **Two SKUs, one kernel:** Research (analysis) and Paper Auto. Live is a gated add-on, not unsupervised.
4. **Steal mechanisms, not repos.** Qlib-shaped factor lab in our code; TradingAgents debate is advisory only; Nautilus/Lean may later sidecar simulation; never become a crypto/vnpy bot.
5. **Sequence truth before theatre:** worker universe + why-no-trade + scan freshness + optional research-list strategy **before** FinBERT, committee UI, or QuantStats PDF factories.
6. Paid bars (Polygon) on the seal/paper path; Yahoo is labeled fallback.

### Consequences (historical)

- Dual UI is a tax; new features land on the desk first.
- Stable mean-reversion stays a named strategy. “Buy the Scan list” is a **separate** strategy flag, not a silent replacement.
- Expanding the worker universe requires batching and rate limits (not a naive 74-ticker Yahoo burst).
- Customer live still requires Stage-2 seal + paper runbook. 24/5 paper may be demoed now.

What no longer holds: using this ADR as the constitution.

---

## ADR-001 — Keep Research UI, Upgrade Execution & Data (history)

**Status:** Superseded (history). Do not cite. ADR-002 is also VOID; current authority is ADR-003.  
**Date:** 2026-09-10  
**Decision ID:** ADR-001

Kept for audit. Original intent: do not rebuild from scratch; keep scoring, RiskEngine, Kelly, mandate, kill switch, Alpaca; upgrade PIT/data and seal evidence; optional Lean sidecar.

What no longer binds: **Streamlit as the primary product UI.**
