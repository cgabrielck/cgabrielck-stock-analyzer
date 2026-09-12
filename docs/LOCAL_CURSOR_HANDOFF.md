# ALPHA//DESK — Local Cursor Handoff Pack

**Last updated:** 2026-09-10  
**GitHub `main` tip (verify with `git pull`):** includes auto-trading upgrade + procurement guide  
**Repo:** `https://github.com/cgabrielck/cgabrielck-stock-analyzer`  
**Local path (user):** `C:\Users\001\cgabrielck-stock-analyzer`

Paste this file (or `@docs/LOCAL_CURSOR_HANDOFF.md`) into your **local Cursor** Agent chat so it inherits context from the Cloud Agent sessions.

**Also required:** `@docs/STRATEGY_ADOPTION.md` — what strategies we already use, what to adopt next, and what is forbidden.

---

## 0. One-line status

Personal US-equity **research terminal + Stage-2 shadow/paper trading scaffold**.  
**Do not rebuild from scratch.** Product UI = FastAPI desk; Streamlit = lab (ADR-002). Keep RiskEngine, Kelly, mandate, kill switch, Alpaca.  
Grade vs a sellable top AI desk: **5.8 / 10**. Operating plan: [`CGAB_WORLD_CLASS_UPGRADE.md`](CGAB_WORLD_CLASS_UPGRADE.md).

---

## 1. Architecture decision (ADR-002) — MUST FOLLOW

**FastAPI desk is the product; Streamlit is lab. Keep risk kernel. No greenfield rewrite.** ADR-001 superseded.

| Keep | Upgrade |
|------|---------|
| Streamlit UI (`backend/app.py`) | Polygon (or paid) equity bars |
| Scoring, regime, Kelly, RiskEngine | Stage-2 multi-month seal evidence |
| Mandate, kill switch, Alpaca adapter | SQLite durable ledger (done); ops alerts |
| Paper/shadow path | Paper validation → small live only after sign-off |

Full text: [`docs/ARCHITECTURE_DECISION.md`](ARCHITECTURE_DECISION.md)

---

## 2. Capability grade (assessment summary)

| Pillar | Score | Note |
|--------|------:|------|
| Research / analysis | 7.5 | Multi-factor + regime + LLM assist |
| Signal generation | 6.0 | Strategies exist; align research↔worker |
| Backtest / validation | 5.5 | Walk-forward exists; PIT file now present |
| Broker / execution | 5.0 | Alpaca paper + shadow; live gated |
| Risk | 7.0 | Kelly, concentration, kill switch, mandate |
| Ops / monitoring | 4.5–5 | Heartbeat/Telegram; harden on VPS |
| Data quality | 4→improving | Yahoo fallback; prefer Polygon when keyed |
| Production readiness | 4.0 | Not unsupervised live yet |

**Market position:** Above hobby Streamlit screeners; below QuantConnect/Lean institutional realism.

---

## 3. What already landed on GitHub (useful code/docs)

### Code / data
- `data/historical_universe.json` — PIT monthly snapshots (IPO-aware)
- `scripts/build_historical_universe.py` — rebuild universe (`--use-polygon` optional)
- `backend/agents/polygon_equity.py` — paid daily bars helper
- `backend/trading/engine/signal_processor.py` — strategy → risk → shadow/paper
- `backend/trading/storage.py` — SQLite (default) / JSON / optional Postgres
- `backend/trading/alpaca_broker.py` — bracket stop / take-profit support
- `backend/trading/performance/tracker.py` — Stage-2 seal gates + snapshot
- `scripts/run_evaluation.py --seal` — write seal artifact
- Worker CLI default **`--mode shadow`**; live needs explicit allow + `APCA_PAPER=false`
- `deploy/` — systemd unit + Hetzner runbook

### Docs (read in this order)
1. **This file** — handoff  
2. [`PROCUREMENT_GUIDE.md`](PROCUREMENT_GUIDE.md) — what to buy  
3. [`ARCHITECTURE_DECISION.md`](ARCHITECTURE_DECISION.md) — keep vs rebuild  
4. [`AUTO_TRADING_ROADMAP.md`](AUTO_TRADING_ROADMAP.md) — stages  
5. [`STAGE2_SEAL_REPORT.md`](STAGE2_SEAL_REPORT.md) — seal template  
6. [`PAPER_VALIDATION_RUNBOOK.md`](PAPER_VALIDATION_RUNBOOK.md) — Stage-3 human gate  
7. [`PAID_DATA_PIT_PLAN.md`](PAID_DATA_PIT_PLAN.md) — Polygon / PIT notes  
8. [`../deploy/README.md`](../deploy/README.md) — VPS deploy  
9. [`../AGENTS.md`](../AGENTS.md) — product enhancement plan + ADR pointer  

---

## 4. Todo / know-list (for local Agent)

### Done (Cloud sessions)
- [x] Competitive assessment + keep-not-rebuild decision  
- [x] PIT historical universe file + builder script  
- [x] Polygon equity helper + worker prefer-Polygon OHLCV  
- [x] SignalProcessor shadow/paper routing  
- [x] Stage-2 seal tooling (`--seal`, report templates)  
- [x] SQLite durable order store + Alpaca brackets  
- [x] Ops notifier wiring + procurement guide  
- [x] Push all of the above to GitHub `main`

### Your manual actions (cannot be coded away)
- [ ] Create local `.env` from `.env.example` (never commit secrets)  
- [ ] Open **Alpaca Paper** keys → `APCA_*` + `APCA_PAPER=true`  
- [ ] Create **Telegram** bot → `TELEGRAM_*`  
- [ ] Buy/rent **Hetzner CPX21** (or equivalent) when ready for 24/5 worker  
- [ ] Buy **Polygon/Massive stocks** (non-Advanced OK) before serious multi-month seal  
- [ ] Run multi-month **shadow** → `python scripts/run_evaluation.py --seal`  
- [ ] Complete **paper validation runbook** (≥30 trading days) before any live  
- [ ] Rotate any API key that was ever pasted/shared (QW8)

### Next engineering (local or Cloud Agent)
- [ ] **Follow [`STRATEGY_ADOPTION.md`](STRATEGY_ADOPTION.md)** — unify research↔worker signals; real RS; ATR risk units; paid bars + shadow seal; Quality split. Do **not** add HFT/MM/stat-arb-neutral/DL-RL-entry without a new ADR.
- [ ] Wire/verify end-to-end: local Streamlit + shadow worker with real Alpaca paper  
- [ ] Confirm `POLYGON_API_KEY` path in worker on Windows + VPS  
- [ ] Accumulate shadow evidence calendar window; fill `STAGE2_SEAL_REPORT`  
- [ ] Deploy systemd worker on VPS per `deploy/README.md`  
- [ ] Only then: small-capital live checklist in `PAPER_VALIDATION_RUNBOOK.md`

### Explicit non-goals (now)
- Do not rewrite the whole app in another framework  
- Do not enable unsupervised live trading  
- Do not buy dual Polygon Advanced (stocks+options) as day-one setup  
- Do not use Taobao/shared API keys as primary market data  

---

## 5. Procurement cheat-sheet

| Phase | Buy / open | Skip |
|-------|------------|------|
| **1 Now** | Alpaca Paper (free), Hetzner CPX21 (~€5–8/mo), Telegram (free), existing LLM | IBKR, QC, Bloomberg, realtime SIP |
| **2 Before seal** | Polygon/Massive **stocks** daily (~$29–79/mo delayed OK) | Stocks Advanced SIP (~$199) |
| **3 After paper gate** | Alpaca Live small size; optional one realtime stock feed; optional options data | Paying twice for SIP |

Details: [`PROCUREMENT_GUIDE.md`](PROCUREMENT_GUIDE.md)

---

## 6. Local vs Cloud Agent — recommendation

| Use **local Cursor Agent** when… | Use **Cloud Agent** when… |
|----------------------------------|---------------------------|
| Editing files under `C:\Users\001\...` | Long unattended tasks / PR factory |
| Filling `.env`, running Streamlit UI | You need Linux/deploy parity |
| Debugging with your keys on disk | Machine can sleep; job continues on VM |
| Day-to-day feature work | Big refactors with CI/PR |

**Recommendation for you now:**  
**Primary = local Cursor Agent** (you already cloned; need `.env` + UI).  
**Cloud Agent = optional** for large background upgrades that open PRs; always `git pull origin main` after.

Sessions do **not** auto-merge. Share context by pointing local Agent at **this file**.

---

## 7. Local bootstrap commands (Windows)

```bat
cd C:\Users\001\cgabrielck-stock-analyzer
git checkout main
git pull origin main

copy .env.example .env
notepad .env

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

streamlit run backend/app.py
```

Shadow worker (after Alpaca paper keys set):

```bat
python -m backend.trading.engine.worker --mode shadow --once
```

Seal evaluation (after you have shadow order history):

```bat
python scripts/run_evaluation.py --seal
```

---

## 8. Safety rules (never violate)

1. Default **shadow/paper**; live requires `APCA_PAPER=false` **and** explicit `--allow-live` (or project equivalent).  
2. LLM / sentiment **must not** bypass RiskEngine, mandate, or kill switch.  
3. Never commit `.env` or real API keys.  
4. No live capital until Stage-2 seal + paper runbook sign-off.

---

## 9. Prompt starter for local Cursor Agent

Copy-paste:

```text
Read docs/LOCAL_CURSOR_HANDOFF.md and docs/PROCUREMENT_GUIDE.md first.
Project: ALPHA//DESK at C:\Users\001\cgabrielck-stock-analyzer.
Rules: ADR-001 keep Streamlit research UI; do not rebuild from scratch;
never bypass risk gates; no live trading without paper runbook.
Task: help me create .env from .env.example, verify the app starts with
streamlit run backend/app.py, and outline the next shadow-trading steps.
Reply in Traditional Chinese unless I ask otherwise.
```

---

## 10. Related PR / history

- PR #1 merged: auto-trading PIT + Stage-2 tooling + durable execution  
- Procurement guide merged to `main` (2026-09-10)  
- Cloud Agent cannot see `C:\Users\...`; local Agent can  

**If anything looks missing after pull:** run `git log -5 --oneline` and confirm tip includes procurement / auto-trading commits on `main`.
