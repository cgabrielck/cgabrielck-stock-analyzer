# ALPHA//DESK — Institutional-Grade Equity Research Terminal

[![Live App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://stock-analyzergit-ijue4vuwb7kuvizn62fema.streamlit.app/)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-191_passing-22d3c5)](#testing--測試)
[![Last commit](https://img.shields.io/github/last-commit/cgabrielck/stock-analyzer)](https://github.com/cgabrielck/stock-analyzer/commits/main)

> A risk-aware US equity research terminal combining fundamental quality, technical signals, active-session prices, market-regime controls, walk-forward backtesting, portfolio sizing, and optional LLM analysis. Designed for individual researchers who want institutional-grade tooling without a Bloomberg terminal bill.
>
> 風險調整型美股研究終端，整合基本面、技術訊號、即時交易時段價格、市場狀態、walk-forward 回測、倉位管理與選用的 LLM 分析。專為個人研究者打造的機構級工具。

**Live application / 線上版本:** [stock-analyzergit-ijue4vuwb7kuvizn62fema.streamlit.app](https://stock-analyzergit-ijue4vuwb7kuvizn62fema.streamlit.app/)

Quality grades, architecture decisions, and the phased enhancement plan are tracked in [docs/QUALITY_ROADMAP.md](docs/QUALITY_ROADMAP.md).

## What's New / 近期更新

- **Chart redesigned** — Clean simple/detail mode. Default view focuses on ~90 recent daily candles with natural scroll, pinch-zoom, and drag panning. No cluttered toolbar — just the price, volume, trend line, entry zone, stop, and target 1. Toggle detail mode for SMA/EMA, Bollinger Bands, RSI, MACD, Fibonacci, and Double Top/Bottom patterns with plain-language indicator explanations.
- **Chart pattern overlays** — Double Top / Double Bottom detection with look-ahead bias prevention, Fibonacci retracement levels, and TradingView Pine Script v6 export for independent verification.
- **Price alert monitoring** — Directional crossing state machine with confirmation bars, re-arm logic (0.5% away from level), stale quote rejection, and duplicate-safe events. Standalone background worker (default 60s interval) checks alerts when the browser is closed.
- **Saved Plan versioning** — Immutable Deep Research versions with version history browsing, replacement with explicit invalidation of old alert rules, and on-demand outcome evaluation at 5/20/60 sessions.
- **Full regression suite** — 191 passing tests covering backtesting, portfolio, alerts, patterns, charts, i18n, and database operations.

## What It Does / 系統功能

- Screens a curated universe of **74 liquid US-listed equities** across 14 sectors.
- Separates the universe into **69 Core** and **5 Satellite** stocks.
- Produces up to five recommendations with sector and speculative-stock limits.
- Selects the correct Yahoo quote for pre-market, regular, after-hours, overnight, or closed sessions.
- Blends fundamental, technical, and optional LLM analysis.
- Applies a transparent risk penalty before ranking stocks.
- Detects the global SPY/VIX market regime and adjusts entry thresholds and portfolio exposure.
- Simulates monthly rebalancing with turnover, cash, transaction costs, and no-look-ahead score calibration.
- Builds a portfolio with capped position weights, stop losses, P&L, correlation alerts, and a local trade journal.
- Provides English, Simplified Chinese, and Traditional Chinese interfaces.

## Current Decision Logic / 目前決策邏輯

### 1. Investable Universe / 可投資股票池

The built-in universe contains 74 stocks:

| Constraint | Current rule |
|---|---:|
| Core stocks | 69 |
| Satellite stocks | 5 |
| Maximum recommendations | 5 |
| Maximum per sector | 2 |
| Maximum Satellite positions | 1 |
| Minimum Satellite selection score | 65 |

Core stocks emphasize liquidity, operating history, and diversified sector exposure. Satellite stocks currently include higher-risk EV and space names. User-added custom tickers are treated conservatively as Satellite stocks.

內建股票池以大型、高流動性股票為核心，並保留少量高風險主題股票。自訂股票預設視為 Satellite，避免未知標的無限制進入投資組合。

### 2. Fundamental Score / 基本面評分

Available metrics are normalized to a 0–100 score. Missing metrics do not automatically become zero; the available weights are renormalized, while live recommendations require at least four usable metrics.

| Metric | Relative weight |
|---|---:|
| Revenue growth | 25% |
| EPS growth | 20% |
| PEG ratio | 15% |
| Profit margin | 10% |
| ROE | 10% |
| Debt-to-equity | 5% |

Yahoo `debtToEquity` is normalized from percentage points to a ratio before scoring. For example, Yahoo `150` becomes `1.5x`.

### 3. Technical Score / 技術評分

Technical analysis uses approximately six months of adjusted OHLCV data:

- RSI(14)
- MACD and signal histogram
- SMA20 / SMA50 trend
- Bollinger Bands
- ATR(14)
- 10-day versus 50-day volume ratio

When technical data is available:

```text
base_score = 70% fundamental_score + 30% technical_score
```

### 4. Optional LLM Blend / 選用 LLM 混合評分

The highest-ranked candidates can be analyzed by any OpenAI-compatible endpoint. The default influence is 20% and is adjustable in the sidebar.

```text
model_score = quant_score × (1 - llm_weight) + llm_score × llm_weight
```

The LLM is an assistant, not the primary decision engine. The app remains fully usable without an API key.

### 5. Risk-Adjusted Selection / 風險調整選股

Risk is calculated from the latest 126 valid adjusted prices, with at least 60 return observations required.

| Risk tier | Trigger | Score penalty |
|---|---|---:|
| Low | Volatility <25% and drawdown <25% | 0 |
| Medium | Volatility ≥25% or drawdown ≥25% | −5 |
| High | Volatility ≥45% or drawdown ≥50% | −10 |

```text
risk_adjusted_score = model_score - risk_penalty
```

Selection, backtest calibration, and calibrated Kelly sizing use `risk_adjusted_score`. The original model score remains visible for auditability. Missing risk data is neutral and clearly labeled instead of silently rejecting the stock.

Additional displayed risk metrics include:

- Historical VaR 95%
- Annualized volatility and return
- Maximum drawdown
- Sharpe and Sortino ratios
- Beta versus SPY

### 6. Global Market Regime / 全局市場狀態

The regime engine uses one year of SPY and VIX data. SPY trend is classified with SMA50 and SMA200; VIX ≥25 overrides the trend as high volatility.

| Regime | Entry threshold | Fill threshold | Target portfolio exposure |
|---|---:|---:|---:|
| Bull | 60 | 50 | 90% |
| Neutral | 65 | 55 | 70% |
| Bear | 72 | 65 | 40% |
| High volatility | 75 | 68 | 40% |

The same regime rules are applied in live recommendations and historical backtests. Defensive regimes raise the quality threshold and retain more cash rather than forcing five positions.

### 7. Active-Session Price Selection / 即時交易時段價格

Price selection follows Yahoo's current `marketState` rather than blindly preferring any populated quote field:

| Yahoo state | Preferred price |
|---|---|
| `PRE` / `PREPRE` | Fresh pre-market quote |
| `REGULAR` | Regular-market quote |
| `POST` / `POSTPOST` | Fresh after-hours quote |
| `CLOSED` | Newest completed-session quote, labeled closed |

If the expected session quote is unavailable or stale, the app requests an explicit one-minute, extended-hours bar. It finally falls back to the regular close and marks the result `STALE`. The UI displays the session, quote source, quote timestamp, and freshness status.

### 8. Portfolio Construction / 投資組合建構

- Maximum position weight: 25%
- Maximum exposure: regime-dependent, between 40% and 90%
- Default fallback: equal weight with retained cash
- Calibrated Kelly: enabled only when a valid walk-forward model exists
- Calibration expiry: 180 days
- Default stop loss: 10%, adjusted by Beta and capped at 25%
- Correlation warning: positive correlation `ρ ≥ 0.80`

The system no longer treats `score / 100` as a win probability. If a valid calibration model is unavailable, it safely uses capped equal weights.

## Walk-Forward Backtesting / Walk-Forward 回測

The backtest uses only information available at each monthly rebalance date:

- Six-month technical warm-up before the requested start date
- Adjusted stock and SPY prices
- Quarterly fundamentals with a conservative 60-day filing lag
- Monthly rebalancing
- Shared live/backtest selection rules
- Historical SPY/VIX regime classification
- Equal-weight or expanding calibrated-Kelly sizing
- Drift-aware turnover and configurable transaction costs
- Cash retained according to regime exposure
- Months with missing selected-stock exit prices are excluded instead of silently reallocating weights
- Coverage diagnostics for prices, technicals, fundamentals, and exits

Calibration uses completed stock-month outcomes only:

```text
At rebalance T:
1. Add outcomes completed before T
2. Fit/update score probability bins
3. Score and size positions at T
4. Queue T outcomes for the next rebalance
```

Calibration models are versioned and written atomically. A model is not allowed into live sizing when universe quality, technical coverage, sample size, or exit-price checks fail.

### Important Backtest Limitation / 重要限制

If `data/historical_universe.json` is absent, historical tests use today's universe and therefore contain survivorship bias. The UI displays this warning, and such a backtest is not permitted to generate a live sizing model.

Yahoo generally exposes only limited quarterly history. Fundamental coverage may therefore be lower in early backtest periods. Coverage is displayed instead of being hidden.

## Data Sources / 資料來源

| Source | Primary use |
|---|---|
| Yahoo Finance / `yfinance` | Session quotes, adjusted OHLCV, fundamentals, benchmark, VIX, options, news |
| Yahoo chart and quote-summary APIs | HTTP fallback pricing and fundamentals |
| Sina Finance | Cloud fallback quotes and news |
| East Money / AKShare | China-accessible fundamental fallback |
| SEC EDGAR | Latest 10-K/10-Q filing context |
| Local seed data | Offline fallback for the original universe subset |
| OpenAI-compatible API | Optional LLM scoring and narrative analysis |

Cached market information uses a five-minute TTL. Running analysis with force refresh bypasses application caches, but upstream providers may still be delayed.

## Professional UI / 專業交易介面

The Streamlit interface uses a custom dark institutional-terminal design:

- Responsive desktop, tablet, and mobile layouts
- Core/Satellite universe labels
- Market-regime and target-exposure banner
- Risk-adjusted recommendation cards
- Model score, risk penalty, and selection-score breakdown
- Dark Plotly technical and backtest charts
- Portfolio risk overview, correlation alerts, stop-loss alerts, and trade journal
- English, 简体中文, and 繁體中文 localization

## Architecture / 系統架構

```text
stock-analyzer/
├── backend/
│   ├── app.py                         Streamlit terminal UI
│   ├── run_agent.py                   CLI entry point
│   ├── i18n.py                        en / zh_cn / zh_tw translations
│   ├── agents/
│   │   ├── data_fetcher.py            Provider fallback orchestration
│   │   ├── technical_analyzer.py      Technical indicators + price risk
│   │   ├── fundamental_analyzer.py    Fundamental scoring
│   │   ├── risk_analyzer.py           Risk metrics + score modifier
│   │   ├── market_regime.py           SPY/VIX regime engine
│   │   ├── recommender.py             End-to-end recommendation pipeline
│   │   ├── portfolio_manager.py       Weights, stops, P&L, correlations
│   │   ├── llm_agent.py               OpenAI-compatible LLM integration
│   │   ├── sec_analyzer.py            SEC filing context
│   │   ├── trading_strategies.py      Per-stock strategy analysis
│   │   └── china_data_fetcher.py      Sina / East Money fallbacks
│   ├── backtesting/
│   │   ├── engine.py                  Monthly walk-forward simulator
│   │   ├── calibration.py             Expanding score calibration
│   │   └── universe.py                Dated universe snapshots
│   └── utils/
│       ├── constants.py               Universe, sectors, score weights
│       ├── selection.py               Shared constrained selector
│       ├── price_utils.py             Session-aware quote selection
│       └── cache.py                   Thread-safe memory/disk cache
├── tests/                              Unit and regression tests
├── .streamlit/config.toml              Production dark theme
├── pytest.ini                          Test path configuration
├── requirements.txt                    Python dependencies
└── runtime.txt                         Streamlit Cloud Python version
```

## Tech Stack / 技術棧

- **Python 3.12**
- **Streamlit** for the web application
- **Pandas** and **NumPy** for time-series and quantitative calculations
- **yfinance** for Yahoo market and fundamental data
- **AKShare**, **Requests**, and **BeautifulSoup4** for fallback data and parsing
- **Plotly** and **Altair** for interactive visualization
- **OpenAI Python SDK** for optional OpenAI-compatible LLM providers
- **pytest** for regression testing
- Thread pools for bounded parallel quote, technical, and backtest data retrieval
- JSON-based local persistence for cache, portfolio state, trade journal, and calibration models

## Quick Start / 快速開始

Python 3.12 is recommended.

```bash
git clone https://github.com/cgabrielck/stock-analyzer.git
cd stock-analyzer

python3.12 -m venv .venv312
source .venv312/bin/activate
pip install -r requirements.txt

streamlit run backend/app.py
```

Open [http://localhost:8501](http://localhost:8501).

CLI mode:

```bash
PYTHONPATH=backend python backend/run_agent.py
```

## API Configuration / API 設定

LLM analysis is optional. Configure an OpenAI-compatible provider in `.env` locally or Streamlit Secrets in cloud deployment:

```env
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=deepseek-chat
LLM_REASONING_MODEL=deepseek-reasoner
ALPHA_VANTAGE_API_KEY=your-alpha-vantage-key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-server-side-service-role-key
```

`deepseek-chat` handles batch scoring and structured JSON tasks. `deepseek-reasoner` is reserved for on-demand single-stock strategy analysis, where deeper reasoning is worth the additional latency and cost.

Do not commit `.env` or API keys. The repository ignores `.env`, virtual environments, caches, portfolio state, and trade journals.

Alpha Vantage is optional. When configured, it supplies company overview, income statements, balance sheets, cash flow, earnings, and adjusted daily-price fallback data. Fundamentals are cached for 24 hours to protect provider quotas. Yahoo remains responsible for session-aware pre-market, regular-market, and after-hours quotes. If Alpha Vantage is unavailable or rate-limited, the existing Yahoo/China/seed fallback chain continues automatically.

## Testing / 測試

```bash
./.venv312/bin/pytest -q
```

Current verified result:

```text
191 passed
```

Additional checks:

```bash
./.venv312/bin/python -m compileall -q backend tests
git diff --check
```

## Streamlit Cloud Deployment / 雲端部署

1. Push the repository to GitHub.
2. Connect it at [share.streamlit.io](https://share.streamlit.io).
3. Set the entry point to `backend/app.py`.
4. Open the app's **Settings → Secrets** and add root-level TOML values:

```toml
ALPHA_VANTAGE_API_KEY = "replace-with-a-new-key"
LLM_API_KEY = "your-llm-key"
LLM_BASE_URL = "https://api.example.com/v1"
LLM_MODEL = "deepseek-chat"
LLM_REASONING_MODEL = "deepseek-reasoner"
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "your-server-side-service-role-key"
```

Only `ALPHA_VANTAGE_API_KEY` is required for Alpha Vantage. The LLM and Supabase values remain optional. Do not add quotes around the variable name, do not paste `.env` syntax such as `KEY=value` into the TOML editor, and never commit `.streamlit/secrets.toml`.
5. Save Secrets, reboot the app if Streamlit does not restart it automatically, and set app visibility as required.

Every push to `main` triggers a Streamlit Cloud redeploy.

## Privacy and Persistence / 隱私與持久化

`data/portfolio_state.json` and `data/trade_journal.json` are local runtime files and are intentionally excluded from Git. On ephemeral cloud hosting, local file changes may be lost after a restart or redeploy. Use an external database before relying on the journal as a permanent multi-user ledger.

### Optional Accounts and Favorites

Guest analysis works without account configuration. To enable persistent username/PIN accounts, Favorites, and preferences:

1. Create a Supabase project.
2. Run `backend/persistence/migrations/001_accounts.sql` through `006_alert_email_delivery.sql` in numeric order in the Supabase SQL editor. Migration 006 retains email-prefixed database column/function names as an internal compatibility layer, but the worker delivers through Telegram only.
3. Add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` to server-side Streamlit Secrets.
4. Restart the app.

The service-role key bypasses row-level security and must never be exposed in browser code or committed to Git. The account adapter is called only by Streamlit server-side Python. Direct access through Supabase's public anon/authenticated roles remains denied because the migration enables RLS without public policies.

The current product requirement stores PIN values as plaintext and allows any PIN format. A database operator or database leak can therefore reveal every PIN. Use unique PINs that are not reused for other services. One-way PIN hashing is strongly recommended before production use.

Authenticated users can save deterministic Deep Research plans, compare a re-analysis with the active plan, explicitly replace it with an immutable new version, review version history, and confirm direction-aware price alert rules. Replacing a plan invalidates its prior alert rules and requires explicit reconfirmation against the new levels.

### Price alert worker

Price alert rules are monitored outside Streamlit so checks continue after the browser closes. Deploy a long-running worker on Railway, Render, or another process host with `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`, then run:

```bash
python backend/alert_worker.py
```

The default interval is one minute. Set `ALERT_INTERVAL_SECONDS` to change it. The worker rejects stale Yahoo quotes, triggers only on directional crossings or entry-zone transitions, re-arms after price moves away from the level, stores duplicate-safe events in the signed-in user's in-app alert inbox, and sends claimed events to the configured Telegram chat with bounded retries. This is periodic/delayed monitoring, not exchange-grade real-time market data. Never commit the bot token, chat ID, or Supabase service-role key.

Deep Research also includes bounded SEC filing evidence with direct EDGAR citations. Saved-plan outcome journals can be evaluated on demand at 5, 20, and 60 completed trading sessions using raw daily OHLC and exact-date SPY comparison. Outcome observations are educational research records, not brokerage fills. The model portfolio reports cash-aware covariance volatility, historical VaR, coverage, and transparent -10%/-20% equity stress scenarios.

## Disclaimer / 免責聲明

This project is for educational and research purposes only. It does not provide financial advice, brokerage execution, or any guarantee of future performance. Market data can be delayed or incomplete. Always verify prices with your broker and perform independent due diligence before trading.

本專案僅供教育與研究用途，不構成投資建議、交易執行服務或任何收益保證。市場資料可能延遲或不完整，下單前請以券商資料為準並自行研究風險。
