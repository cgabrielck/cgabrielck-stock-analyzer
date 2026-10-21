# Modular architecture (under ADR-003)

## Status

**This document binds under ADR-003 + Addendum A (Accepted, 2026-10-21).**  
See [`ARCHITECTURE_DECISION.md`](ARCHITECTURE_DECISION.md). Visual twin: [`boomboom-modular-architecture.html`](boomboom-modular-architecture.html).

Authority order: ADR-003 > this file > `AUDIT_REMEDIATION_PLAN_2026-10-21.md` (Confirmed by Gabriel 2026-10-21; **not in this checkout**) > frozen roadmaps.

This is architecture documentation. It does **not** authorize trading-runtime work, deploys, or changes to live RiskEngine / Kelly / mandate / kill settings.

---

## 1. Surfaces

| Surface | Path | Role under ADR-003 |
|---------|------|--------------------|
| **Desk (product)** | `backend/api/app.py` + `web/static/` | The product UI. Scan / Deep / Auto / paper evidence live here. |
| **Streamlit (lab / legacy)** | `backend/app.py` | Lab and legacy research UI. Not the commercial surface. |
| **Worker (execution clock)** | `backend/trading/engine/worker.py` | Paper / shadow execution. Guarded by mandate, kill, RiskEngine. |
| **Lab extras** | `backend/lab/` | Optional citation-try modules. Default off. Never place orders. |

Two SKUs share one kernel (score, regime, RiskEngine, Kelly, mandate, kill, Alpaca paper). Live remains gated.

---

## 2. Keep vs do not rewrite

**Keep:** RiskEngine, mandate (`config/mandate.json`), kill switch, Kelly sizer, Alpaca `BrokerAdapter`, scoring DNA, “LLM / Telegram cannot place orders.”

**Do not rewrite in this architecture:** those live settings. ADR-003 is modular *around* the kernel, not a replacement of it.

**Do not wire:** `backend/lab/advisory_debate.py` → `OrderManager` (see §8).

---

## 3. Dual-clock and H1–H3

ADR-003 requires **two clocks**. They already exist as separate processes in this repo; they must not be collapsed into one “the bot woke up so the scan is fresh” loop.

| Clock | Owner today | Evidence | May do | Must not do |
|-------|-------------|----------|--------|-------------|
| **Research / Scan clock** | FastAPI Desk lifespan | `backend/api/scan_scheduler.py` (US session cadence); freshness `SCAN_STALE_AFTER_HOURS` / `SIGNAL_MAX_AGE_HOURS` in `backend/api/research_jobs.py` | Birth of scores and of **L1 intents** (H1 Propose) | Place broker orders |
| **Execution / Worker clock** | Trading worker | `backend/trading/engine/worker.py`; operator cadence `backend/trading/engine/cadence.py` | L2 submit after **H3 Merge**; exits / SELL still run when research is stale | Invent a new Intent; treat a stale Scan as a fresh proposal |

Fail-closed rule already on `main`: stale Scan/signal blocks **new buys**; exits still run (`docs/PAPER_VALIDATION_RUNBOOK.md` SIGNAL max-age). ADR-003 keeps that split.

### Harness horizons H1–H3

These names are ADR-003 harness stages. They are **not** identifiers in Python on `main` (721d04d). Map them onto the two clocks as follows:

| Horizon | Name | Clock | What happens | Who |
|---------|------|-------|--------------|-----|
| **H1** | Propose | Research / Scan | A `HarnessProposal` plus Intent v0 rows is written to **IntentStore** with `status=proposed` | Desk / named strategy / operator tool — not Telegram text, not lab debate |
| **H2** | Approve | Operator on Desk | Human approval. LLM / Telegram / `advisory_debate` **cannot** approve | Desk operator |
| **H3** | Merge | Execution clock, only after H2 | Approved intents become `merged` and **eligible** for L2. Merge is not a fill and not a bypass of RiskEngine / mandate / kill | Worker may then size and submit |

```text
Research clock                         Execution clock
     │                                        │
     ├─ H1 Propose ──► IntentStore            │
     │                      │                 │
     ├─ H2 Approve ─────────┤                 │
     │                      ▼                 │
     │                 status=merged ── H3 ──► L2 OrderManager
     │                                        │
     │                              RiskEngine / mandate / kill
     │                                        ▼
     │                                   BrokerAdapter
```

---

## 4. L1 vs L2

| Layer | Name | Owns | Must not own |
|-------|------|------|----------------|
| **L1** | Intent | Symbol, side, stop (intent-level), conviction_tier, expiry, provenance (`as_of`, `source`), harness status | Share count, notional, broker ticket, fills |
| **L2** | Execution | Kelly / RiskEngine sizing, `Order` (`backend/trading/models.py`), `OrderManager`, `OrderStore`, broker legs, fills, slippage | Inventing an Intent that skipped H1–H2 |

Current code is **L2-heavy**. `OrderStore` implementations in `backend/trading/storage.py` persist `Order` objects (quantity, `order_type`, `idempotency_key`, fills). That is an order ledger, **not** IntentStore. Do not document `JSONOrderStore` as IntentStore.

Backtest comment “L1/2 definition” in `backend/backtesting/factor_lab.py` is the **½(stock + cash) turnover formula**. It is unrelated to ADR-003 L1/L2.

---

## 5. Intent v0 schema

**Binds under ADR-003 Addendum A.** Intent v0 is L1 only.

Canonical JSON shape (docs contract; not a Pydantic model on `main` as of 721d04d):

```json
{
  "schema_version": "intent.v0",
  "intent_id": "01JEXAMPLEINTENTID",
  "symbol": "AAPL",
  "side": "buy",
  "stop": {
    "type": "pct",
    "value": 0.10
  },
  "conviction_tier": "medium",
  "expiry": "2026-10-22T20:00:00Z",
  "as_of": "2026-10-21T14:30:00Z",
  "source": "harness_proposal:01JEXAMPLEPROPOSAL",
  "status": "proposed"
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `schema_version` | yes | Must be `"intent.v0"` |
| `intent_id` | yes | Stable unique id |
| `symbol` | yes | US equity / ETF ticker |
| `side` | yes | `buy` \| `sell` |
| `stop` | yes | Intent-level protective stop (`type` + `value`). Not an Alpaca bracket ticket |
| `conviction_tier` | yes | `low` \| `medium` \| `high` |
| `expiry` | yes | RFC3339 UTC. Past expiry → `expired`; H3 must not merge |
| `as_of` | yes | Research-clock timestamp; stale vs `SIGNAL_MAX_AGE_HOURS` fail-closes new buys |
| `source` | yes | Must point at a `HarnessProposal` id |
| `status` | yes | `proposed` \| `approved` \| `merged` \| `rejected` \| `expired` |

`stop` may be `null` only for explicit `side=sell` flatten intents. Buys require a stop.

### Forbidden L2 fields on Intent v0

An Intent v0 object **must not** include:

- `quantity`, `qty`, `shares`, `notional`, `dollar_amount`
- `limit_price`, `stop_price` as broker triggers, `order_type`, `order_class`
- `take_profit_price` as a broker leg
- `broker_order_id`, `client_order_id`, `idempotency_key`
- `filled_qty`, `filled_avg_price`, `filled_at`, `slippage_pct`, `commission`
- `status` values from `backend/trading/models.py` `OrderStatus` (`draft`, `risk_approved`, `submitted`, `accepted`, `partially_filled`, `filled`, `cancelled`, `reconciliation_error`)

Those belong on `Order` after H3, sized by the existing kernel.

Existing `Order` fields that look similar (`side`, `stop_loss_price`) are **L2**. Do not copy an `Order` into IntentStore and call it Intent v0.

---

## 6. HarnessProposal and Proposal → Approve → Merge

`HarnessProposal` is the L1 envelope. Same provenance as Intent v0: architecture contract under ADR-003; **no class of this name on `main`**.

```json
{
  "schema_version": "harness_proposal.v0",
  "proposal_id": "01JEXAMPLEPROPOSAL",
  "created_at": "2026-10-21T14:30:00Z",
  "as_of": "2026-10-21T14:30:00Z",
  "clock": "research",
  "author": "desk_operator",
  "rationale": "named strategy research_list; Scan as-of inside SIGNAL max-age",
  "intent_ids": ["01JEXAMPLEINTENTID"],
  "status": "proposed"
}
```

| Step | Horizon | Allowed writers | Effect |
|------|---------|-----------------|--------|
| **Propose** | H1 | Desk / named strategy path | Rows land in IntentStore as `proposed` |
| **Approve** | H2 | Human on Desk | `approved`. LLM, Telegram, and `run_advisory_debate` cannot take this step |
| **Merge** | H3 | Execution clock after H2 | `merged`. Worker may build an L2 `Order` and send it through SignalProcessor → RiskEngine → OrderManager |

Reject and expiry are terminal. There is no “auto-approve if the advisory committee liked it” path.

---

## 7. IntentStore

**IntentStore** is the durable L1 ledger for `HarnessProposal` + Intent v0.

| | IntentStore (L1, ADR-003) | OrderStore (L2, code today) |
|--|---------------------------|-----------------------------|
| Path | **Not implemented** on `main` 721d04d | `backend/trading/engine/order_manager.py` protocol; `JSONOrderStore` / SQLite / Postgres in `backend/trading/storage.py` |
| Records | Proposals + Intent v0 | `Order` / fills |
| Clock | Research clock for writes; execution clock reads merged rows | Worker / broker |
| May call `OrderManager` | No | Yes |

Do not extend `OrderStore` with Intent v0 fields as a shortcut. That would collapse L1 into L2.

---

## 8. Hard boundary — `lab/advisory_debate` ≠ product M7

**Short note (binding under ADR-003):**

`backend/lab/advisory_debate.py` is a **lab / advisory memo** (Bull / Bear / Risk draft). It is **not** product milestone M7. It **must not** be wired to `OrderManager`, the worker buy path, IntentStore Merge, or H2 Approve.

Evidence already on `main`:

- Module docstring: “Advisory Bull/Bear/Risk debate — research draft only. Never places orders.” and “still never calls OrderManager / worker buys.”
- `POST /api/lab/advisory` in `backend/api/app.py` — “Does not call OrderManager.” Forces `places_order = False`.
- `tests/lab/test_desk_slice_b.py` — `test_advisory_module_never_mentions_submit_order`, `test_worker_module_does_not_import_advisory`, `test_llm_cannot_set_places_order`.
- `docs/LAB_CITATION_TRY.md` — worker ignores advisory rewrites.
- `docs/CGAB_WORLD_CLASS_UPGRADE.md` Slice B — “Advisory committee on Deep (cost-capped, **not** wired to OrderManager).” That Slice B memo is still not M7.

Wave 3 on the frozen upgrade doc is “Desk tear + advisory does not place orders.” Wave 7 there is the **live gate**, which is also not this lab module. Whatever product M7 is specified to be in the missing audit plan, it is **not** `run_advisory_debate`.

If any older sentence can be read as “committee buys” or “wire debate into OrderManager,” it is VOID under this section.

---

## 9. Mapping to code on `main` (721d04d)

| ADR-003 name | In repo today? | Path |
|--------------|----------------|------|
| Desk product | Yes | `web/static/index.html`, `backend/api/app.py` |
| Streamlit lab | Yes | `backend/app.py` |
| Research clock | Yes | `backend/api/scan_scheduler.py`, `backend/api/research_jobs.py` |
| Execution clock | Yes | `backend/trading/engine/worker.py`, `cadence.py` |
| L2 Order / OrderManager / OrderStore | Yes | `backend/trading/models.py`, `engine/order_manager.py`, `storage.py` |
| RiskEngine / mandate / kill | Yes | `backend/trading/risk/gates.py`, `safety/mandate.py`, `safety/kill_switch.py` |
| Lab advisory | Yes (must stay off L2) | `backend/lab/advisory_debate.py` |
| Intent v0 / HarnessProposal / IntentStore | **No** — docs contract only | this file §5–§7 |
| Product M7 | **Not a module in this checkout** | must not be aliased to lab advisory |

---

## 10. Frozen documents

These files remain in tree as history. They do not bind:

- `docs/PAPER_VALIDATION_RUNBOOK.md`
- `docs/AUTO_TRADING_ROADMAP.md`
- `docs/NEXT_PHASE_PLAN.md`
- `docs/QUALITY_ROADMAP.md`
- `PROGRESS_LOG.md`

Paper **operational** checklists inside the runbook (kill switch, `APCA_PAPER=true`, filled ≠ accepted) stay useful as evidence procedure, but architecture preconditions now point at ADR-003, not ADR-002.
