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

### 2026-07-30 - Alert rule save UX

- Removed the redundant alert-rule confirmation checkbox from the saved-plan
  UI at user request.
- Users can now select rules and click Save directly. Saving with no selected
  rules remains supported and clears existing monitoring rules.
- Removed the unused confirmation label from all three languages.
- Updated the translation-key contract test to remove the retired
  `alerts.confirm` key.
- Verification passed: 208 tests and `git diff --check`. Deployment is pending
  a commit and push; `.vscode/settings.json` remains excluded.

### 2026-07-30 - Production worker smoke check

- Railway successfully loaded four enabled LLY alert rules from Supabase.
- Worker logs reported `rules: 4` and `stale: 4`, confirming the database and
  scheduler paths work while correctly rejecting quotes older than the
  20-minute alert-safety limit.
- No alert was evaluated, triggered, or delivered from stale market data.

### 2026-07-30 - Option safety core implementation started

- Confirmed scope: option safety core plus auditable agent-stage trace.
- Confirmed lifecycle policy: an option entry alert is an opportunity only;
  stop and target monitoring starts only after the user confirms a simulated
  fill price and quantity in the app.
- Migration 007 will be append-only. It will expand allowed option event types,
  add an owned `option_positions` lifecycle table, keep option exit rules
  disabled before entry confirmation, and expose an atomic confirmation RPC.
- The worker will use a contract-specific option-chain quote adapter rather
  than the stock-session quote helper for OCC symbols.
- `.vscode/settings.json` remains unrelated and must not be changed or staged.

### 2026-07-30 - Option safety core implementation checkpoint

- Added append-only migration 007 with option event types and a manual-entry
  lifecycle. Entry opportunities disable themselves after one trigger; stop and
  targets cannot run before user confirmation.
- Stop is terminal, target one is one-shot, and target two closes the simulated
  position. Open or entry-alerted positions block alert-rule and saved-plan
  replacement so protective rules cannot be silently removed.
- Added a contract-specific Yahoo option-chain adapter. Entry evaluates the ask,
  exits evaluate the bid, zero-bid/overwide markets fail closed, and monitoring
  is limited to regular US option-market hours. Yahoo's last-trade timestamp is
  explicitly labeled as a proxy rather than a true bid/ask timestamp.
- Added deterministic option stages for data, liquidity, volatility, payoff,
  event risk, and risk judgment. Missing earnings-calendar evidence is shown as
  skipped rather than inferred.
- Added `ALERT_OWNER_USER_ID`; Railway filters both rules and Telegram delivery
  claims to one account UUID, preventing cross-user alert disclosure.
- Independent review identified lifecycle races and replacement hazards; all
  high-severity findings were addressed before migration deployment.
- Final concurrency review added shared option-position locking across entry,
  stop, target, alert replacement, and saved-plan replacement. Mutually
  exclusive terminal events cannot both insert after one closes the lifecycle.
- Option monitoring now also requires Yahoo's underlying market state to be
  `REGULAR`, preventing holiday and early-close alerts that fixed clock hours
  alone would incorrectly allow.
- Deployment ordering is deliberate: apply migration 007 and configure
  `ALERT_OWNER_USER_ID` before pushing/redeploying the worker, because the new
  worker fails closed when owner scoping is missing.

### 2026-07-30 - Migration 007 deployed

- User configured Railway `ALERT_OWNER_USER_ID` for the Stock Analyzer account
  that owns the monitored plans.
- `007_option_alert_lifecycle.sql` completed successfully in Supabase with
  `Success. No rows returned`.
- Production object verification is the final checkpoint before committing and
  pushing the new option worker and UI.
- Post-deployment verification passed for all five required objects: the
  `option_positions` table, manual-entry RPC, evaluation RPC, owner-scoped
  delivery RPC, and option event-type constraint.
- The option safety release is ready to commit and push. Full local suite:
  219 tests passed; compilation, diff, and secret checks passed.

### 2026-07-30 - Worker runtime import hotfix

- Post-push Railway inspection showed logs from the prior deployment while the
  new commit was propagating.
- A runtime-only annotation issue was found proactively: `alert_worker.py`
  referenced `Optional` without importing it. Static bytecode compilation did
  not execute the module and therefore did not detect this.
- Added the missing import and a runtime import/annotation test before pushing
  the worker hotfix.

### 2026-07-30 - Option-safe worker production smoke test

- Railway deployed hotfix commit `54598c2` successfully.
- Two consecutive production cycles reported four owner-scoped rules, four
  evaluations, zero stale/rejected quotes, zero triggers, and zero deliveries.
- This confirms the migration 007 RPC contract, owner filtering, stock alert
  regression path, fresh-quote evaluation, and worker runtime startup are
  operating together in production.
- Next checkpoint: create one non-production option plan, verify only
  `option_entry` starts enabled, and exercise manual simulated-entry activation
  before testing stop and targets.

### 2026-07-30 - Yahoo options rate-limit handling

- A Deep Research option-chain request returned Yahoo `Too Many Requests`.
- The UI previously appended the provider exception to the no-liquidity text,
  incorrectly implying that the contract screen had completed successfully.
- Added stable provider error codes, a five-minute per-ticker rate-limit
  cooldown that also applies to forced refreshes, and dedicated three-language
  provider warnings. Rate limiting is no longer classified as no trade.

### 2026-07-30 - Empty Yahoo option response classification

- TSLA returned `no_options` in the cloud even though a local Yahoo request
  returned 21 expiration dates, confirming an incomplete provider response
  rather than an actual absence of listed options.
- Empty expiration lists and empty chains now fail closed as
  `provider_incomplete` and start a five-minute per-ticker cooldown. They no
  longer appear as a liquidity-screen failure and never create a trade plan.

### 2026-07-30 - Cboe delayed option fallback

- Added Cboe's public delayed option-chain endpoint as the automatic fallback
  for Yahoo rate limits, empty expiration lists, empty chains, and request
  failures.
- A live TSLA verification normalized 6,388 contracts across 25 expirations,
  including bid/ask, volume, open interest, IV, and Greeks.
- Cboe data is explicitly labeled delayed in the plan, Agent trace, and UI.
  Entry still uses ask, exits use bid, and all freshness/liquidity hard gates
  remain enforced. If both providers fail, the ticker enters a five-minute
  cooldown rather than creating a plan.
- Security review tightened the boundary: Cboe fallback is research-only and
  can show contracts and Greeks, but it cannot create a saved option plan or
  trigger Railway alerts. Only Yahoo's verifiable regular-session chain remains
  eligible for actionable option monitoring.
- Added a 30-second in-memory Cboe payload cache to avoid repeatedly downloading
  the multi-megabyte chain, consistent put/call field semantics, option-contract
  change detection, invalid-market rejection, and prior-session warnings.
- Live TSLA verification returned 185 calls and 185 puts for the selected
  expiration with Delta/Gamma/Theta/Vega, and correctly marked the snapshot as
  delayed and prior-session. Full suite: 233 tests passed.
