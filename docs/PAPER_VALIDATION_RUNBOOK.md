# Paper Validation Runbook (Stage 3 Gate)

Last updated: 2026-09-10

This runbook is the **human-time** gate between Stage 2 shadow seals and any
small-capital live trading. Code cannot skip calendar time.

## Preconditions

1. ADR-001 accepted (`docs/ARCHITECTURE_DECISION.md`) — keep research UI; upgrade execution/data.
2. `data/historical_universe.json` present (built via `scripts/build_historical_universe.py`).
3. Stage 2 seal artifact `backend/data/performance_shadow.json` with `seal_passed: true`
   for a window of **≥ 30 calendar days** (prefer ≥ 90).
4. Kill switch, mandate (`config/mandate.json`), audit JSONL, and Telegram ops
   notifications verified in paper/shadow.
5. Worker durable ledger: `ORDER_STORE_BACKEND=sqlite` (or postgres) on the VPS.
6. `APCA_PAPER=true` for the entire paper validation window.

## Daily checklist (paper)

| Step | Action | Done |
|------|--------|------|
| 1 | Confirm systemd worker heartbeat fresh (< 2× interval) | |
| 2 | Confirm kill switch disengaged unless intentional halt | |
| 3 | Review overnight fills / rejects in Trading monitor | |
| 4 | Confirm no mandate breaches in audit log | |
| 5 | Record daily P&L vs SPY (Telegram summary or dashboard) | |
| 6 | If daily loss ≥ 3% or kill switch auto-trip → halt and investigate | |

## Promotion thresholds (paper → small live)

| Metric | Required |
|--------|----------|
| Paper window | ≥ 30 trading days |
| Sharpe (paper) | ≥ 1.0 |
| Max drawdown | ≤ 15% preferred / ≤ 20% hard stop |
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
