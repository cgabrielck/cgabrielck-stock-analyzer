# Upgrade Recovery Log

This file is the durable checkpoint for ongoing upgrades. Update it before and
after each upgrade batch so work can resume safely after an editor or process
crash.

## Confirmed Decisions

- Historical validation uses a 2-3 year window, not five years.
- News sentiment remains a bounded score modifier: +3% for very positive
  sentiment, -3% for very negative sentiment, and 0% otherwise.
- Existing unrelated working-tree changes must be preserved.

## Recovery Snapshot - 2026-07-29

Repository state at recovery:

- Branch: `main`
- HEAD: `bf8ed6d` (`docs: improve README intro, badge, and changelog`)
- HEAD matches `origin/main`.
- No staged changes, merge, or rebase was present.
- Interrupted work consisted of 12 modified tracked files and 7 untracked
  files.

Recovered upgrade streams:

1. Sentiment, dollar-volume analysis, and five-pillar timing score.
2. Option alerts, notification delivery, and Railway worker deployment.

Recovered files that are required by imports and must not be discarded:

- `backend/agents/sentiment_analyzer.py`
- `backend/agents/signal_pillars.py`
- `backend/telegram_notifier.py`
- `backend/persistence/migrations/006_alert_email_delivery.sql`
- `railway.toml`

## Upgrade Checklist

| Upgrade | State | Verification / Notes |
|---|---|---|
| Recovery log and resumable checkpoints | In progress | This file created before new edits. |
| Sentiment aggregation and bounded +/-3% modifier | Verified | Focused scoring suite passed. |
| Five-pillar timing score | Verified | Boundary, coverage, audit-detail, and integration tests passed. |
| Ranking UI and i18n for timing/sentiment | Verified | Modules compile; ranking and recommendation cards expose the new fields in all three languages. |
| Historical validation limited to 2-3 years | Verified | UI offers 2/3 years; engine defaults to a rolling 3 years; default-window test passed. |
| Option alerts and Telegram delivery | Verified | 28 Telegram/alert/account tests passed; atomic claims and retry behavior are migration-backed. |
| Correlation heatmap and risk badge | Verified | Portfolio/risk/regime suite passed and UI modules compile. |
| Regime-specific scoring weights | Verified | Live and historical five-pillar scores use the same tested regime weights. |
| Full regression test | Verified | 206 tests passed on 2026-07-29. |

## Known Historical Validation Limits

- Historical constituent snapshots are not available, so current-universe
  backtests retain survivorship bias.
- `yfinance` does not provide a complete point-in-time fundamental archive.
- Results must disclose fundamental coverage and must not be presented as a
  five-year institutional-grade validation.

## Session Log

### 2026-07-29 - Recovery and scoring batch started

- Reconstructed the interrupted work from Git status, history, source files,
  and the previous enhancement plan.
- Confirmed that committed work was not lost.
- Confirmed the +/-3% sentiment policy with the user.
- Confirmed that historical validation must use only 2-3 years.
- Next checkpoint: add scoring tests, expose ranking fields, and change the
  backtest default/window guidance.

### 2026-07-29 - Scoring and validation window implementation

- Made empty-news aggregation return the same schema as non-empty results.
- Added focused tests for sentiment aggregation, composite bounds, and the
  confirmed +/-3% score modifier.
- Added focused tests for five-pillar scoring, missing-data renormalization,
  audit details, and score bounds.
- Limited the backtest UI to 2 or 3 years and changed the engine default to a
  rolling three-year window.
- Verification is the next action; entries remain marked pending until tests
  pass.
- Added timing, sentiment, sentiment adjustment, and dollar-volume columns to
  the ranking UI, plus a sentiment summary on recommendation cards.
- Added Simplified Chinese, Traditional Chinese, and English labels for the new
  UI fields.
- Verification checkpoint: 20 focused tests passed, changed Python modules
  compiled successfully, and `git diff --check` reported no errors.

### 2026-07-29 - Alert delivery hardening

- Found and removed a duplicate-email race caused by selecting pending events
  before marking them as in progress.
- Migration 006 now atomically claims events with `FOR UPDATE SKIP LOCKED`,
  recovers claims abandoned for ten minutes, backs off failed retries, and
  limits delivery to five attempts.
- Tightened option alert generation to require positive entry/stop premiums and
  two positive target premiums.
- Added SMTP content/configuration tests and worker success/failure tests.
- Migration 006 still must only be deployed after migrations 004 and 005 are
  confirmed in Supabase.

### 2026-07-29 - Regime scoring weights

- Added auditable five-pillar weight profiles for bull, neutral, bear, and
  high-volatility regimes.
- Bull markets emphasize trend and momentum; bear/high-volatility markets
  emphasize mean reversion and risk quality.
- Applied the same profiles to live recommendations and each historical
  rebalance date to prevent live/backtest model drift.
- Added tests for profile normalization and custom-weight score behavior.
- Preserved precomputed technical scores when a degraded/test data source lacks
  raw five-pillar inputs.
- Added an interactive portfolio correlation heatmap with three-language title
  labels and retained explicit warnings for pairs at or above 0.80.
- Verification checkpoint: 26 alert/email/account tests passed. A separate
  35-test regime/backtesting/risk/portfolio suite passed. Changed modules
  compiled successfully and `git diff --check` remained clean.

### 2026-07-29 - Final verification checkpoint

- Full suite: 206 tests passed in 84.53 seconds.
- Only two third-party `py_mini_racer` deprecation warnings were emitted; they
  target Python 3.19 behavior and do not indicate an application failure.
- Python compilation checks passed and `git diff --check` is clean.
- Marked `backend/agents/github_upgrade_plan.md` as a historical snapshot and
  pointed it to this log so stale implementation notes are not mistaken for
  current status.
- No commit, migration deployment, email send, Railway deployment, or secret
  change was performed.

## Resume From Here

The recovered implementation is complete and locally verified. Migration 006
is deployed; Telegram and Railway configuration are the remaining operational
work:

1. Create a Telegram bot and obtain the destination chat ID.
2. Configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SUPABASE_URL`, and
   `SUPABASE_SERVICE_ROLE_KEY` in the Railway worker environment.
3. Trigger one non-production stock alert and one option alert, then verify the
   event changes from `sending` to `sent` without duplicate Telegram messages.
4. Deploy `railway.toml` only after the database smoke test succeeds.

The 2-3 year backtest remains intentionally labeled with its constituent and
point-in-time fundamental coverage limitations.

### 2026-07-29 - Migration 006 deployment fix

- Supabase rejected migration 006 because migration 005 defined
  `record_alert_evaluation(...)` with return type `void`, while migration 006
  attempted to replace it with return type `uuid`.
- Kept the function return type as `void`; the worker does not consume an event
  ID, so dropping and recreating a production function is unnecessary.
- Restored idempotent `ON CONFLICT DO NOTHING` event insertion. Migration 006
  remains safe to rerun because its schema operations use `IF NOT EXISTS` or
  explicitly replace their own constraint/function definitions.

### 2026-07-29 - Migration 006 deployed

- User confirmed migrations 004 and 005 were present in Supabase.
- The corrected `006_alert_email_delivery.sql` completed successfully with
  `Success. No rows returned`.
- Post-deployment checks confirmed all five required objects: `email_status`,
  `email_attempts`, `email_claimed_at`, `claim_alert_email_deliveries`, and
  `record_alert_email_delivery`.
- This SMTP checkpoint was superseded by the Telegram-only decision below.

### 2026-07-29 - Telegram-only notification decision

- User selected Telegram and explicitly declined email delivery.
- Replaced SMTP configuration and runtime delivery with Telegram Bot API
  settings: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- Removed the email notifier and its tests; added an HTML-safe Telegram notifier
  for stock and option events.
- Kept migration 006's `email_*` columns and RPC names as an internal database
  compatibility layer because migration 006 is already deployed. They now
  represent generic delivery state; no email is sent.
- Worker and repository Python APIs now use generic delivery terminology.
- Telegram-focused verification passed: 28 tests, compilation checks, and no
  remaining Python SMTP references.
- Telegram-only full regression verification passed: 208 tests. The only two
  warnings remain third-party `py_mini_racer` Python 3.19 deprecations.
- `git diff --check` passed. No Telegram token, chat ID, or Supabase secret was
  written to the repository.

### 2026-07-29 - Telegram bot created

- User created the Telegram bot, started a private conversation, and obtained
  the destination chat ID through `getUpdates`.
- No bot token or chat ID was stored in repository files.
- Next checkpoint: commit and push the verified Telegram-only worker before
  configuring Railway deployment secrets.

### 2026-07-29 - Commit preparation

- Updated README migration and worker instructions for migration 006 and
  Telegram-only delivery.
- Secret scan found no Telegram bot token, chat ID, or real Supabase service
  role key in repository files.
- `.vscode/settings.json` is a local interpreter change and is intentionally
  excluded from the upgrade commit.
