# Architecture Decisions

**Current decision: ADR-003 + Addendum A (Accepted, 2026-10-21).**  
**ADR-002 is VOID.** ADR-001 remains history only.

Binding order (highest first):

1. This file — **ADR-003 + Addendum A**
2. [`MODULAR_ARCHITECTURE.md`](MODULAR_ARCHITECTURE.md)
3. `AUDIT_REMEDIATION_PLAN_2026-10-21.md` (Confirmed by Gabriel 2026-10-21; **not in this checkout**)
4. Older roadmaps (`AUTO_TRADING_ROADMAP`, `NEXT_PHASE_PLAN`, `QUALITY_ROADMAP`, `PAPER_VALIDATION_RUNBOOK`, `PROGRESS_LOG`, `CGAB_WORLD_CLASS_UPGRADE`) — frozen / superseded; not authority

Visual map: [`boomboom-modular-architecture.html`](boomboom-modular-architecture.html)

This file records architecture authority only. It does **not** change live RiskEngine / Kelly / mandate / kill settings or trading runtime code.

---

## ADR-003 — Modular desk, Intent L1, dual-clock harness

**Status:** Accepted  
**Date:** 2026-10-21  
**Decision ID:** ADR-003  
**Voids:** ADR-002  
**Supersedes:** ADR-002 (VOID), ADR-001 (history)  
**Addendum:** Addendum A (below)  
**Operating architecture:** [`MODULAR_ARCHITECTURE.md`](MODULAR_ARCHITECTURE.md)

**Provenance:** On 2026-10-21 the PM (Gabriel) stated ADR-003 + Addendum A is **Accepted**. At `origin/main` `721d04d` and every other remote branch then in this checkout, no file named ADR-003 / Addendum A / `MODULAR_ARCHITECTURE.md` / `AUDIT_REMEDIATION_PLAN_2026-10-21.md` / `boomboom-modular-architecture.html` existed. This record is the docs-sync write-down of that Accepted decision, mapped to paths that **do** exist in the repo. It is not a silent rewrite of risk, mandate, or kill settings.

### Context

ADR-002 correctly named the FastAPI desk as the product UI and Streamlit as lab. It is nonetheless **VOID** as binding authority: later work needs a modular L1/L2 split (Intent vs order), a dual-clock harness (H1–H3), and an explicit ban on treating `backend/lab/advisory_debate.py` as product M7 or wiring it to `OrderManager`.

### Decision

1. **Do not greenfield-rewrite the repo.** Keep RiskEngine, mandate, kill switch, Kelly, Alpaca `BrokerAdapter`, scoring DNA, and the rule that LLMs / Telegram cannot place orders. This docs batch does not change those live settings.
2. **Product UI is the Desk:** FastAPI (`backend/api/app.py`) serving `web/static`. **Streamlit (`backend/app.py`) is lab / legacy**, not the commercial surface.
3. **Two SKUs, one kernel:** Research (analysis) and Paper Auto. Live is a gated add-on, not unsupervised.
4. **Modular layers:** L1 = Intent (HarnessProposal → IntentStore). L2 = execution (`OrderManager`, broker, fills). Intent v0 **must not** carry L2 fields. See Addendum A and [`MODULAR_ARCHITECTURE.md`](MODULAR_ARCHITECTURE.md) §4–§5.
5. **Dual-clock, H1–H3:** Research/Scan clock and Execution/Worker clock must not be collapsed. Harness flow is **Propose (H1) → Approve (H2) → Merge (H3)**. Merge does not bypass RiskEngine / mandate / kill.
6. **Hard boundary:** `backend/lab/advisory_debate.py` is **not** product M7 and **must not** be wired to `OrderManager`. Evidence today: module docstring, `POST /api/lab/advisory`, `tests/lab/test_desk_slice_b.py`.
7. **Steal mechanisms, not repos.** TradingAgents-style debate stays advisory / lab. Never become a crypto/vnpy bot.
8. Paid bars (Polygon) on the seal/paper path; Yahoo is labeled fallback.

### Consequences

- New product features land on the Desk first. Streamlit may receive critical lab fixes only.
- `OrderStore` / `JSONOrderStore` (`backend/trading/storage.py`) is the **L2 order ledger**, not IntentStore. IntentStore is the L1 target under this ADR; it is **not implemented** in Python on `main` as of 721d04d.
- Older roadmaps that still say “Streamlit is the product / research UI” or that bind ADR-001 / ADR-002 are frozen. Conflicts resolve here.
- Customer live still requires Stage-2 seal + paper runbook. 24/5 paper may be demoed now.

### Addendum A — Intent v0, L1 vs L2, harness (Accepted)

**Status:** Accepted (part of ADR-003)  
**Binds:** [`MODULAR_ARCHITECTURE.md`](MODULAR_ARCHITECTURE.md) §4–§7

**L1 (allowed on Intent v0):** `schema_version`, `intent_id`, `symbol`, `side`, `stop`, `conviction_tier`, `expiry`, `as_of`, `source`, `status` (`proposed` | `approved` | `merged` | `rejected` | `expired`).

**L2 (forbidden on Intent v0):** `quantity`, `notional`, `limit_price`, `order_type`, `order_class`, `broker_order_id`, `idempotency_key`, `filled_*`, `slippage_pct`, `commission`, `take_profit_price` as a broker leg. Sizing and broker tickets belong to RiskEngine / Kelly / `OrderManager` after H3 Merge.

**HarnessProposal** is the only supported write path into IntentStore: Propose → Approve (human / Desk; never LLM or Telegram) → Merge. Merged intents may become eligible for L2; they are not orders.

Full schema and clock table: [`MODULAR_ARCHITECTURE.md`](MODULAR_ARCHITECTURE.md) §3–§7.

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

What still holds is restated under **ADR-003**. What no longer holds: using this ADR as the constitution, or treating any Wave/committee/lab debate as an execution path.

---

## ADR-001 — Keep Research UI, Upgrade Execution & Data (history)

**Status:** Superseded (history). Do not cite. ADR-002 is also VOID; current authority is ADR-003.  
**Date:** 2026-09-10  
**Decision ID:** ADR-001

Kept for audit. Original intent: do not rebuild from scratch; keep scoring, RiskEngine, Kelly, mandate, kill switch, Alpaca; upgrade PIT/data and seal evidence; optional Lean sidecar.

What no longer binds: **Streamlit as the primary product UI.**
