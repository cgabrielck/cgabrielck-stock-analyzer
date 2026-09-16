# Paper Validation Runbook (Stage 3 Gate)

Last updated: 2026-09-16

This runbook is the **human-time** gate between Stage 2 shadow seals and any
small-capital live trading. Code cannot skip calendar time.

**Paid services:** only open Alpaca Live / realtime SIP / options data after this
runbook is signed. Until then stay on Phase 1–2 purchases in
[`PROCUREMENT_GUIDE.md`](PROCUREMENT_GUIDE.md).

**Agent order:** Wave 0–8 in the unified Cgab roadmap. This file is the paper
seal / adoption gate; it does not authorize jumping to committee (Wave 3) or
an IC factory (Wave 4).

## Preconditions

1. ADR-002 accepted (`docs/ARCHITECTURE_DECISION.md`) — FastAPI desk is product UI;
   keep RiskEngine / Kelly / mandate / kill / Alpaca paper.
2. `data/historical_universe.json` present (built via `scripts/build_historical_universe.py`).
3. Stage 2 seal artifact `backend/data/performance_shadow.json` with `seal_passed: true`
   for a window of **≥ 30 calendar days** (prefer ≥ 90).
4. Kill switch, mandate (`config/mandate.json` from `config/mandate.example.json`),
   audit JSONL, and Telegram ops notifications verified in paper/shadow.
5. Worker durable ledger: `ORDER_STORE_BACKEND=sqlite` (or postgres) on the VPS.
6. `APCA_PAPER=true` for the entire paper validation window.

## Daily checklist (paper)

| Step | Action | Done |
|------|--------|------|
| 1 | Confirm systemd worker heartbeat fresh (< 2× interval) | |
| 2 | Confirm kill switch disengaged unless intentional halt | |
| 3 | Review Desk / Telegram **日報**: filled vs new/pending, skip summary, paper vs SPY net of costs | |
| 4 | Confirm no mandate breaches in audit log | |
| 5 | Confirm Scan/signal as-of is inside SIGNAL max-age (see below). Stale → worker fail-closed, no new buys | |
| 6 | If daily loss ≥ 3% or kill switch auto-trip → halt and investigate | |

**Filled ≠ accepted.** `new` / `accepted` / working limits mean the broker has the
order, not that shares are on the book. Unfilled limits are **not** a seal.
Only `filled` counts toward paper evidence vs SPY.

## SIGNAL max-age (fail-closed)

Worker refuses **new buys** when Scan/signal as-of is older than
`SIGNAL_MAX_AGE_HOURS` (defaults to `SCAN_STALE_AFTER_HOURS`, 72h — one US
session buffer, Fri close → Mon open). Heartbeat `scan_stale`, Desk banner, and
Telegram `research_stale` use the same window.

- Last **real** fund scores are kept (not rewritten to 50).
- Exits / SELL still run.
- Missing Scan book is stale → no new buys.
- Override: set `SIGNAL_MAX_AGE_HOURS` in `.env`; tighter of Scan vs SIGNAL wins.

Copy `config/mandate.example.json` → `config/mandate.json` (no secrets; gitignored).
Wave 1 pins the Desk universe, `$10k` notional/order, 5 BUY orders per UTC day.
Do not shrink `allowed_symbols` to mega-caps (that would silently change
`research_list` / Stable). Do not raise the notional cap to bypass Kelly / Stage-2.

## Adoption freeze (strategy parameters)

Changing a **named** strategy default (`stable` / `trend` / `breakout` /
`research_list` / `adaptive` knobs, Stage-2, Kelly fraction, `$10k` book) is an
**adoption**, not a hotfix.

Required **before** merging a default-parameter change:

1. **Pre-declare** the candidate (name, old vs new values, hypothesis) in the
   bakeoff / shadow log — no “backtest looked good so we shipped it.”
2. **OOS / walk-forward** on US equity sessions only, **net of stated costs**,
   vs SPY for the same window.
3. **MaxDD** of the candidate ≤ runbook hard stop (20%) on that OOS window;
   prefer ≤ 15%.
4. **Named strategy** — never silently rewrite `research_list` or Stable into
   “buy the Scan.”
5. Paper **filled** trades (not working limits) still must beat SPY net of costs
   before anyone claims alpha.

Forbidden: lowering Stage-2 / Kelly / `$10k`; enabling live; letting LLM or
Telegram place orders; treating unfilled limits as a seal.

## Promotion thresholds (paper → small live)

| Metric | Required |
|--------|----------|
| Paper window | ≥ 30 trading days |
| Sharpe (paper) | ≥ 1.0 |
| Max drawdown | ≤ 15% preferred / ≤ 20% hard stop |
| Excess vs SPY | **net of stated costs** > 0 (unfilled limits do not count) |
| Reconciliation | zero unresolved broker vs ledger mismatches for 5 consecutive days |
| Human approval | signed below |

## Live enablement (Stage 3 only)

1. Set small mandate caps (`max_notional_per_order`, `max_total_exposure`).
2. Keep kill switch path rehearsed (`python -m backend.trading.engine.worker --halt`).
3. Start worker with **`--mode paper` first**; only after sign-off use live keys with
   `APCA_PAPER=false` **and** `--allow-live`.
4. Prefer broker-native bracket stops (`order_class=bracket`) for new entries.
5. First live week: human reviews every new BUY before raising automation.

## Sign-off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Strategy owner | | | |
| Risk reviewer | | | |

**Without this sign-off, Stage 4 automation remains forbidden.**
