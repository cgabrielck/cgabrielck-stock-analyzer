# ALPHA//DESK — System Map

**Last checked:** 2026-09-28  
**Scope:** Architecture, HTTP API, desk portals, and settings, taken from the running code paths. This file does not claim a live broker session was exercised.

## Verification

| Check | Result |
|-------|--------|
| `python3 -m compileall -q backend tests scripts` | Exit 0 |
| `python3 -m pytest -q` | **455 passed**, 2 warnings (`websockets.legacy`, `pkg_resources`) in 15.54s |
| Relative markdown links in repo-root `*.md` and `docs/**/*.md` | 59 checked, 0 missing |
| Live Alpaca / Polygon / Telegram | Not called. Missing keys are configuration, not a test failure. |

Warnings do not fail the suite. Paper fills, shadow seal evidence, and beating SPY were not re-run in this check.

## Architecture

ADR-002: two SKUs, one risk kernel. Do not greenfield-rewrite. See [`ARCHITECTURE_DECISION.md`](ARCHITECTURE_DECISION.md).

```text
Product UI   FastAPI desk  backend/api/app.py
             static page   web/static/index.html   (GET /)
Lab UI       Streamlit     backend/app.py          (not the sold surface)
Research     agents + research/ + backtesting/
Execution    worker -> SignalProcessor -> RiskEngine -> OrderManager
             -> Alpaca paper  OR  ShadowTradingEngine
Safety       mandate, kill switch, half-Kelly caps, reconciliation
```

Start the desk:

```bash
python3 -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

Lab (separate process):

```bash
streamlit run backend/app.py
```

Worker (default shadow; live needs `APCA_PAPER=false` and an explicit allow flag):

```bash
python3 -m backend.trading.engine.worker --mode shadow --once
```

`web/src/` plus `web/index.html` is a Vite React shell. FastAPI serves `web/static/index.html` only. The React app is not on the product path.

## HTTP API

All routes are registered in [`backend/api/app.py`](../backend/api/app.py). Order routes call `_require_paper()` and return HTTP 403 when `APCA_PAPER` is not true.

| Method | Path | Role |
|--------|------|------|
| GET | `/` | Desk HTML (`web/static/index.html`) |
| GET | `/api/health` | Liveness `{status, name}` |
| GET | `/api/meta` | Paper flag, market-hours override, scan freshness, universe size, public LLM config |
| GET | `/api/account` | Alpaca paper account summary |
| GET | `/api/positions` | Open paper positions |
| GET | `/api/orders` | Orders (`status`, `limit`) |
| POST | `/api/orders` | Submit a paper order |
| POST | `/api/orders/{order_id}/cancel` | Cancel a paper order |
| GET | `/api/ops` | Kill-switch and ops snapshot |
| POST | `/api/ops/kill` | Halt new buys (`reason`) |
| POST | `/api/ops/resume` | Clear the kill switch |
| GET | `/api/ai-mode` | Saved AI Mode / strategy settings |
| PUT | `/api/ai-mode` | Update AI Mode settings (does not start the worker) |
| GET | `/api/worker/status` | Heartbeat, running flag, why-no-trade |
| POST | `/api/worker/start` | Start the paper/shadow worker |
| POST | `/api/worker/stop` | Stop the worker |
| GET | `/api/guide` | Onboarding steps (EN / zh) |
| GET | `/api/watchlist` | Local watchlist |
| POST | `/api/watchlist` | Add a symbol |
| DELETE | `/api/watchlist/{symbol}` | Remove a symbol |
| GET | `/api/universe` | Curated universe |
| GET | `/api/quotes` | Quote strip (`limit`) |
| GET | `/api/chart/{symbol}` | OHLCV (`period`, default `6mo`) |
| POST | `/api/scan` | Start a scan job |
| GET | `/api/scan/status` | Scan job status |
| GET | `/api/scan/latest` | Latest scan result |
| POST | `/api/deep` | Start deep research |
| GET | `/api/deep/status` | Deep-research job status |
| GET | `/api/deep/latest` | Latest deep-research result |
| GET | `/api/research/{symbol}` | Research card for one symbol |
| GET | `/api/options/{symbol}` | Options chain (research; not an auto-trade path) |
| GET | `/api/calendar` | Catalyst / calendar events |
| GET | `/api/journal` | Trade journal entries |
| POST | `/api/journal` | Append a journal note |
| GET | `/api/performance` | Paper book vs SPY (net of costs) plus optional stable backtest summary (`refresh`) |

## Portals (desk sidebar)

Defined in [`web/static/index.html`](../web/static/index.html). Operator guide: [`CGAB_USER_GUIDE.md`](CGAB_USER_GUIDE.md).

| Group | Page id | Label |
|-------|---------|-------|
| Navigation | `desk` | Desk (holdings, manual ticket) |
| Navigation | `scan` | Scan |
| Navigation | `orders` | Orders |
| Navigation | `research` | Research |
| Navigation | `market` | Market |
| Navigation | `chart` | Chart |
| Apps | `aimode` | AI Mode (strategy + Start/Stop paper auto) |
| Apps | `strategies` | Strategies |
| Apps | `alerts` | Alerts |
| Apps | `calendar` | Calendar |
| More | `guide` | Guide |
| More | `journal` | Journal |
| More | `options` | Options |
| More | `settings` | Settings |

Top bar: `PAPER` badge, `AUTO ON` / `AUTO OFF` from worker heartbeat. Saving AI Mode sliders does not start the worker.

The Settings page is read-only status: language (EN / 繁), paper flag, ignore-market-hours, LLM on/off and model names, worker AUTO, kill switch, and a link to the user guide. Secrets are not edited in the browser.

## Settings (environment)

Copy [`.env.example`](../.env.example) to repo-root `.env`. Placeholders that contain `replace-with` are not configured. Loader: [`backend/config.py`](../backend/config.py) (`get_secret`, Supabase account settings, Telegram settings).

| Phase | Variables | Role |
|-------|-----------|------|
| 1 Paper / shadow | `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `APCA_PAPER=true` | Broker. Desk blocks live when paper is not true. |
| 1 | `IGNORE_MARKET_HOURS` | Local scan gate only; broker may still reject fills. |
| 1 | `WORKER_UNIVERSE_CAP`, `WORKER_FETCH_BATCH`, `WORKER_FETCH_PAUSE_SEC` | Universe cap and Yahoo pacing. |
| 1 | `SCAN_AUTO`, `SCAN_INTERVAL_MIN`, `SCAN_USE_LLM`, `SCAN_OUTSIDE_HOURS`, `SCAN_STALE_AFTER_HOURS` | Desk auto-scan. |
| 1 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ALERT_OWNER_USER_ID` | Ops alerts and alert-worker owner. Placeholder tokens count as off. |
| 1 | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_REASONING_MODEL`, `LLM_TIMEOUT_SECONDS` | Research scores and deep write-ups. Worker does not call the LLM every minute. Optional `LLM_MACRO_MODEL`, `LLM_COMPLEX_MATH_MODEL`. |
| 1 | `ORDER_STORE_BACKEND`, `ORDER_STORE_PATH` | Durable ledger (SQLite default). |
| 1 | `ALERT_INTERVAL_SECONDS` | Price-alert worker interval. |
| 2 Bars | `POLYGON_API_KEY`, `POLYGON_BASE_URL`, `ALPHA_VANTAGE_API_KEY` | Paid daily bars / fundamentals. Yahoo remains fallback. |
| 3 Live | `APCA_PAPER=false`, `DATABASE_URL` | Only after [`PAPER_VALIDATION_RUNBOOK.md`](PAPER_VALIDATION_RUNBOOK.md). Not part of this check. |
| Optional | `TRADIER_API_TOKEN`, `TRADIER_BASE_URL` | Options research quotes. |
| Optional | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Server-side account storage. Never expose the service-role key to the browser. |

## Files checked and kept

Deleted this pass: repo-root `9_up.md` (contents were the single token `rmb`; no references).

Kept on purpose:

| Path | Why it stays |
|------|----------------|
| `TRADING_PHILOSOPHY.md`, `PROGRESS_LOG.md`, `TRADE_JOURNAL.md`, `UPGRADE_RECOVERY_LOG.md` | Read by the reflection agent and `.opencode` resume flow. |
| `backend/agents/github_upgrade_plan.md` | Stale snapshot, still cited by the recovery log. |
| `backend/agents/china_data_fetcher.py`, `backend/agents/auto_upgrader.py`, `backend/pine_export.py` | Still imported. |
| `docs/*.md`, `skills/trading/` | Linked from AGENTS / handoff / README, or used as agent skills. |
| `web/src/`, `web/index.html`, `web/package.json` | Vite app. Not served by FastAPI; not dead source. |
| `for_record.md` | Unreferenced price-comparison note. Not the procurement spec ([`PROCUREMENT_GUIDE.md`](PROCUREMENT_GUIDE.md) is). Kept so the note is not lost. |

## Related docs

- [`STRATEGY_ADOPTION.md`](STRATEGY_ADOPTION.md) — what to cite and what is forbidden.
- [`AUTO_TRADING_ROADMAP.md`](AUTO_TRADING_ROADMAP.md) — shadow / paper / live stages.
- [`CGAB_USER_GUIDE.md`](CGAB_USER_GUIDE.md) — operator clicks.
