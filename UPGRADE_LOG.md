# ALPHA//DESK — Upgrade Log

This file is the persistent memory for all LLM sessions working on this project.
Every upgrade, decision, and architectural change is recorded here.
**Always read this file at the start of a new session before making changes.**

---

## Session Log

### Session: 2026-08-12 — Phase 1: Auto-Trading Strategy Engine

**Goal:** Build a full auto-trading system on top of the existing paper trading infrastructure.

#### Decisions Made (User-Confirmed)
- **Strategies:** Both Stable + Aggressive implemented simultaneously
- **Worker deployment:** Phase 1 = Streamlit in-process thread; Phase 2 = cloud VPS ($5-6/mo)
- **Backtest window:** 3 years of historical data
- **Broker:** Alpaca Paper Trading (APCA_PAPER=True), endpoint = https://paper-api.alpaca.markets/v2
- **SDK:** Migrated from `alpaca-trade-api` → `alpaca-py` (websockets conflict fix)
- **Stocks scope:** US equities/ETFs only for now — NO options auto-trading (Alpaca options data is 15-min delayed on free tier)

#### Reference Projects Studied
- **Freqtrade:** Strategy separation architecture (indicators / entry / exit layers)
- **QuantConnect/Lean:** Universe selection decoupled from strategy logic
- **MLfinlab (Lopez de Prado):** Purged K-Fold CV to prevent overfitting in backtests
- **Qlib (Microsoft):** AI factor caching layer
- **vectorbt:** Vectorized pre-screening before full backtest

#### Reference Strategies Implemented
- **Stable:** Bollinger Band mean-reversion + RSI oversold + fundamental score filter
- **Aggressive:** Minervini VCP breakout + volume surge + SMA50 trend filter
- **Risk:** Kelly Criterion position sizing + correlation check (ρ < 0.7) + daily loss halt

---

## Architecture Overview

```
Streamlit UI (frontend)
    │
    ├── Trading Page → render_trading_dashboard()
    │       ├── Strategy Selector (Stable / Aggressive / Hybrid)
    │       ├── Worker Start/Stop toggle
    │       ├── Account summary + positions
    │       ├── Order ledger
    │       └── Manual signal injection
    │
    └── TradingWorker (background thread)
            │  polls every 60s during market hours
            │
            ├── for each ticker in STOCK_UNIVERSE:
            │       ├── fetch latest OHLCV (yfinance)
            │       ├── strategy.generate_signal()
            │       ├── if signal → RiskEngine 2.0 → SignalProcessor → AlpacaBroker
            │       └── check_exit() on open positions → sell if triggered
            │
            ├── RiskEngine 2.0:
            │       ├── Max order notional
            │       ├── Buying power check
            │       ├── Position concentration check
            │       ├── Correlation check (new) — rejects if ρ > 0.7 with existing positions
            │       ├── Daily loss halt (new) — stops trading if portfolio down > 3% today
            │       └── VIX dampening (new) — halves position size if VIX > 30
            │
            └── Kelly Position Sizer (new):
                    f* = (p×b - q) / b, capped at 25% of portfolio

```

---

## File Map (New/Modified Files This Session)

| File | Status | Description |
|------|--------|-------------|
| `backend/trading/strategies/__init__.py` | NEW | Strategy package init |
| `backend/trading/strategies/base.py` | NEW | Abstract StrategyBase, Signal, ExitSignal models |
| `backend/trading/strategies/stable.py` | NEW | Bollinger + RSI mean-reversion strategy |
| `backend/trading/strategies/aggressive.py` | NEW | VCP breakout + momentum strategy |
| `backend/trading/strategies/registry.py` | NEW | Strategy registry, get_strategy() factory |
| `backend/trading/risk/position_sizer.py` | NEW | Kelly Criterion position sizing |
| `backend/trading/risk/gates.py` | UPGRADED | Added correlation check, daily loss halt, VIX dampening |
| `backend/trading/engine/worker.py` | UPGRADED | Full signal polling loop replacing `pass` stub |
| `backend/trading/ui/dashboard.py` | UPGRADED | Strategy selector, Worker start/stop, auto-trade panel |
| `backend/agents/pattern_analyzer.py` | UPGRADED | Added detect_vcp() for Minervini VCP pattern |
| `UPGRADE_LOG.md` | NEW | This file — persistent LLM memory |

---

## Known Technical Debt / Future Work

### Phase 2 (Next Session)
- [ ] Migrate TradingWorker to independent process (`python -m backend.trading.engine.worker`)
- [ ] Deploy to DigitalOcean VPS ($5-6/mo) with systemd service
- [ ] Deploy Streamlit UI to Streamlit Cloud (free)
- [ ] ShadowTradingEngine 2.0: simulate fills using next-bar open price + slippage, not instant fill

### Phase 3
- [ ] Backtest both strategies over 3 years with Purged K-Fold CV
- [ ] Output: Win%, Profit Factor, Max DD, Sharpe per strategy
- [ ] Add Hyperopt-style parameter optimization (Bayesian search)

### Phase 4
- [ ] Pairs trading (cointegration-based statistical arbitrage)
- [ ] Market regime detection (HMM or VIX-based) → adjust strategy weights by regime
- [ ] Post-trade analysis dashboard (attribution, slippage tracking)
- [ ] Full reconciliation (local ledger vs Alpaca positions, auto-correct discrepancies)

### Phase 5 (Live Trading Readiness)
- [ ] Switch APCA_PAPER=False only after 30-day paper trading with positive Sharpe > 1.0
- [ ] Add Alpaca Data Plus ($99/mo) for SIP real-time data
- [ ] Add email/Telegram notifications for fills, risk halts, daily P&L

---

## Codebase Notes for LLMs

- **Python version:** 3.12 (.venv312), NOT 3.14 (venv is a stale secondary env)
- **Run app:** `.venv312/bin/streamlit run backend/app.py`
- **Run tests:** `.venv312/bin/python -m pytest -q`
- **app.py sys.path fix:** `backend/app.py` top-of-file inserts project root into sys.path so `from backend.xxx import` works under `streamlit run backend/app.py`
- **pytest.ini:** `pythonpath = backend` — tests use relative imports from backend/
- **Broker:** AlpacaBroker uses alpaca-py SDK (NOT alpaca-trade-api which is deprecated and conflicts with websockets)
- **i18n:** 3 languages in `backend/i18n.py` — zh_cn (line 4), zh_tw (line 909), en (line 1815). Always add keys to all 3 blocks.
- **STOCK_UNIVERSE:** 44 stocks defined in `backend/utils/constants.py`
- **Cache:** Thread-safe file-based cache in `backend/utils/cache.py`
- **Trading data path:** `trading_orders.json` in project root (created by JSONOrderStore)

---

## Upgrade History

| Date | Session | Key Changes |
|------|---------|-------------|
| 2026-08-06 | Pre-session | Built AlpacaBroker, OrderManager, RiskEngine, ShadowTradingEngine, SignalProcessor, JSONOrderStore, ReconciliationService, Trading UI, Onboarding tour, PWA rebranding to ALPHA//DESK |
| 2026-08-12 | Session 1 | Fixed alpaca-trade-api→alpaca-py migration (websockets conflict), fixed `from backend.xxx` import path for Streamlit, fixed scan.ready i18n missing zh_cn key, added K-line indicator selector panel |
| 2026-08-12 | Session 2 | **Phase 1:** Strategy engine (Stable + Aggressive), VCP detection, Kelly sizing, RiskEngine 2.0, Worker signal polling, Trading UI strategy panel |
| 2026-08-13 | Session 3 | **平台與部署策略：** 記錄 Alpaca vs Futu 費用比較、PLAN 1–4（Basic 驗證 / Alpaca Plus / FutuBroker / 多用戶部署）、4GB→8GB 升級路徑、多用戶限制與業務風險 |
| 2026-08-13 | Session 4 | **Phase 2 工程：** 獨立 Worker CLI（含 live 保護 + heartbeat）、Shadow 2.0 真實成交模擬（下一根 K 開盤 ± 滑點 / 限價觸及）、策略回測引擎（Win%/PF/Sharpe/DD）。全套件 335 測試通過 |

### Session: 2026-08-12 — Alpaca Paper Connection Verification

- Read this log before diagnosing the Trading page.
- Verified the local `.env` contains the three required runtime values: `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, and `APCA_PAPER=True`. Secrets are intentionally not recorded here.
- Read-only Alpaca account query succeeded using `.venv312`: `paper=True`, account ready, portfolio value `$100,000`, cash `$100,000`, buying power `$400,000`, positions `0`.
- `APCA_End_point=https://paper-api.alpaca.markets/v2` is currently informational. `AlpacaBroker` uses `alpaca-py` `TradingClient(..., paper=True)`, which selects the Paper endpoint automatically.
- Security action required: the user pasted the API secret into chat. The exposed Paper credentials must be revoked and regenerated in Alpaca before further use. Never place replacement secrets in this log or chat.
- Operational limitation: Phase 1 Worker is an in-process Streamlit background thread. It is not a 24/7 service; it stops when the Streamlit process restarts or the server is stopped. Cloud/VPS independent-worker deployment remains Phase 2.
- User regenerated the exposed Paper credentials, tested the Trading dashboard and automatic-trading flow, and confirmed the Paper Trading path is functioning correctly.
- Current operational baseline: Alpaca Paper Trading is connected and verified. Keep `APCA_PAPER=True`; do not move to live trading before the 30-day paper-trading and Phase 3 backtest gates documented above are complete.

---

## Session: 2026-08-13 — 平台與部署策略 (Alpaca vs Futu)

**目標：** 記錄本次討論的數據平台、券商執行、多用戶部署的最終決策與取捨，供後續 LLM 記憶。

### 用戶確認的目標規模
- 自動交易：最多 3 人
- 股票分析：最多 5 人
- 第一優先：4GB VPS + Alpaca Algo Trader Plus
- 強烈想找更便宜方案，認真考慮用 Futu/Futubull 取代 Alpaca 以省下數據費

### 費用比較（已事實查核）
- Alpaca 美股/ETF/期權佣金：**$0**（零售 API 用戶，官方明示）
- Alpaca Algo Trader Plus：**$99/月 = 行情數據訂閱，不是交易費**；實盤交易本身不需要 Plus
- Alpaca Basic：免費；IEX 實時、30 個 WebSocket symbol、歷史 200 次/分鐘
- Futu HK 美股：佣金 `$0.0049/股`（每單最低 `$0.99`）+ 平台費 `$0.005/股`（每單最低 `$1.00`）
- Futu HK 港股：佣金 HK$0，但每單平台費 **HK$15**
- Futu 美股期權：每合約 `$0.15` 起（最低 `$1.99`）+ `$0.30/合約` 平台費
- Futu 融資利率：美股 4.8%、港股 6.8%（年）；Alpaca 融資利率 6.25%
- 例：買 10 股 AAPL（約 $2000）：Alpaca = $0；Futu ≈ $1.99/單
- Futu API 交易本身不加收 API 費用，費用按 App 原本佣金結構

### PLAN 1 — Alpaca Basic（免費）單人驗證 【成本：$0 數據費 + VPS】
- 範圍：目前單用戶 Paper Trading，不需實時流
- 44 隻股票日線歷史可用歷史接口輪詢（200 次/分足夠）
- 完成：獨立 TradingWorker、Shadow Trading 2.0、3 年回測
- VPS：DigitalOcean 4GB 約 HK$188/月；Hetzner 4GB 約 HK$34–60/月
- 可行性：**高**；建議先做這步，先不買任何數據方案
- **現在不要買 Alpaca Plus**

### PLAN 2 — Alpaca Algo Trader Plus + VPS 【成本：約 HK$776 數據 + VPS】
- 範圍：單人、實時 SIP（全美交易所）+ OPRA 期權、無限股票 WebSocket
- VPS 4GB 約 HK$188/月 → 合計約 HK$965/月；8GB 約 HK$345 → 約 HK$1,121/月
- 升級路徑：DigitalOcean 支援 4GB→8GB Resize；先停 Worker、做 Snapshot、挑美股收盤後操作
- 可行性：**單人高**；**不適合 3–4 人**（每人數據授權需獨立）

### PLAN 3 — Futu/Futubull API 全面取代 Alpaca 【成本：$0 數據/月 + 每筆交易費】
- Futu 可**完整覆蓋**目前用到的 Alpaca 功能：帳戶、訂單、持倉、購買力、Paper & Live、美股/ETF/期權/期貨、經 OpenD 實時報價
- 需要新增 `FutuBroker` 實作現有 `BrokerAdapter`，策略/風控/UI 全部保留
- 架構：ALPHA//DESK Worker → Futu OpenD（跑在 VPS）→ Futu 伺服器
- 必須驗證的限制（尚未確認）：
  * **OpenD** 是必須常駐的網關程序（多一層監控/重啟/解鎖/重連）
  * 實時訂閱額度按帳戶資產分檔：<HK$10k=100；≥HK$10k=300
  * 歷史 K 線**每 7 天配額**（100/300/1000/2000 依資產）——回測反覆調參可能卡到
  * 美股期權實時 LV1 免費需帳戶資產 ≥ **US$3,000**，否則要買 OPRA 行情卡
  * 美股 LV2 實時目前是促銷/免費狀態——必須用自己帳戶實際確認
  * Live 下單前需 `unlock_trade`（交易密碼）；外網 Live 建議加密通道 + RSA key
  * **沒有 OAuth**：每個用戶要登入自己的 OpenD
- 單人可行性：**中**（約 1–2 週實際開發）
- 3–4 人可行性：數據成本優勢**真實**（3 人 Alpaca Plus = $297/月 vs Futu $0 數據），但每人 OpenD/帳戶/配額/維運複雜度才是真正成本

### PLAN 4 — 多用戶部署（3 自動交易 / 5 分析）【成本依方案】
- 架構：共享市場數據與策略服務；每個用戶有自己 BrokerAdapter、風控參數、訂單/持倉資料庫、Worker namespace、獨立 Kill Switch
- 現況：程式碼是**單帳戶**（一個 `.env`、一個 `AlpacaBroker`、一個 `JSONOrderStore`）→ **尚未準備好**
- 需要的改造：
  * 多用戶身分與帳戶隔離
  * 每用戶獨立 Broker 連線 + 加密儲存憑證（不能用共享 `.env`）
  * 每用戶獨立策略設定與風控限制
  * 用 PostgreSQL 取代單一 JSON 檔
  * Worker heartbeat / 告警 / 每人 Kill Switch
  * WebSocket 連線上限管理
- 我們面對的限制 / 阻礙：
  * Alpaca Trading API + Algo Trader Plus 是**單用戶零售方案**；多人轉售/再分發實時數據可能需 Broker API / 商業授權（Broker API 實時期權從約 $1,000+/月起）——用戶選擇暫不處理合規，**記錄為業務風險，非法律意見**
  * Futu：無 OAuth；每個用戶必須用自己的 Futu 帳戶 + OpenD 登入 + 自己數據配額
  * **絕不能共享一個券商帳戶或一組 API Key 給多個用戶**
  * 建議多人時升到 8GB VPS（4GB 留給單人/Phase 1）
- 可行性：現況**中低**；需要先完成上述 Plan 4 改造才能跑

### 最終建議（供後續 Session 記憶）
- Phase 1（現在）：Alpaca Basic（免費）+ 本地/4GB VPS + 單人 Paper + 回測。數據費 $0。
- Phase 2：策略驗證後再決定——單人（Alpaca Basic 一直免費）vs 3–4 人產品（那時認真做 FutuBroker；多人場景 Futu 數據成本優勢真實）
- 保持 `BrokerAdapter` 抽象，讓 Alpaca / Futu / IBKR 可替換
- 等 3 年回測完成後再重訪此決定

---

## Session: 2026-08-13 — Phase 2 執行（獨立 Worker / Shadow 2.0 / 策略回測）

**目標：** 執行 PLAN 1 驗證路徑的前三項工程，全部 $0 數據費。

### 已完成
1. **獨立 TradingWorker CLI**（`python -m backend.trading.engine.worker`）
   - 新增 `main()`：`--strategy / --interval / --tickers / --heartbeat-file / --allow-live / --once`
   - 優雅處理 SIGTERM/SIGINT；heartbeat JSON 供 systemd/監控輪詢
   - **Live 保護**：`APCA_PAPER != true` 且未加 `--allow-live` 時拒絕啟動
   - 新增測試 `tests/trading/engine/test_worker_cli.py`（6 個）
2. **ShadowTradingEngine 2.0**（`backend/trading/engine/shadow.py`）
   - 市價單：下一根 K 開盤價 ± 滑點（預設 5bps）
   - 限價買：後續 K 線 Low ≤ 限價才成交；限價賣：High ≥ 限價才成交；未觸及 → CANCELLED
   - 記錄 `filled_avg_price` / `slippage_pct` 到 Order（models.py 新增欄位）
   - 支持部分成交（`quantity_touched`）與無前向資料回退
   - 新增測試 `tests/trading/engine/test_shadow_v2.py`（8 個）
3. **策略回測引擎**（`backend/backtesting/strategy_backtest.py`）
   - 每日頻率，對 Stable/Aggressive/Hybrid 逐一執行 generate_signal / check_exit
   - 成交使用 Shadow 2.0 模型；輸出 Win% / Profit Factor / Sharpe / Max DD / 交易數
   - 使用 yfinance 現有數據 + 250 日 warmup；**已知局限：current-universe 存活者偏差、基本面中性分數**（point-in-time 基本面免費源不可得，已記錄）
   - 新增測試 `tests/backtesting/test_strategy_backtest.py`（6 個）

### 驗證
- 完整測試套件：**335 passed**（新增 21 個）

### 真實 3 年冒煙測試（5 隻：AAPL/MSFT/NVDA/AMZN/GOOGL，2023-08 → 2026-08）
| 策略 | 交易數 | 總回報 | 勝率 | 獲利因子 | Sharpe | 最大回撤 |
|---|---|---|---|---|---|---|
| stable | 17 | +2.13% | 70.6% | 2.92 | 1.14 | 0.87% |
| aggressive | 11 | -0.76% | 27.3% | 0.67 | -0.28 | 2.08% |
| hybrid | 51 | -0.10% | 58.8% | 1.23 | -0.07 | 1.17% |
- 註：Aggressive 在此 5 隻大盤股 + 3 年區間表現不佳——真實結果，說明需更廣股票池或參數調優（這正是回測的用途）。已知局限：current-universe 存活者偏差、基本面中性分數。

### 待辦（下一步）
- [ ] 用真實 3 年數據跑三個策略回測，輸出報告
- [ ] 將結果寫入 README / docs 作為策略門檻依據
- [ ] 部署層：systemd service 檔 + VPS（DigitalOcean 4GB 或 Hetzner 4GB）
- [ ] 決定：單人維持 Alpaca Basic 免費 vs 多用戶做 FutuBroker

---

### Session: 2026-08-13 — Full-Universe Strategy Backtest, Tuning Prep, VPS Research

**Goal:** Expand strategy backtest to the full configured universe, tune Stable/Aggressive parameters from evidence, record results, research cheaper VPS with comparable stability/power, run full pytest, and commit.

#### Working-forest state (uncommitted changes from previous session)
- `backend/agents/llm_agent.py` — `_create_completion()` now retries up to 3× with exponential backoff (2s, 4s) before falling back to the chat model; validates non-empty `choices`.
- `backend/backtesting/engine.py` — `fetch_price_data()` accepts a `max_workers` override (defaults `MAX_WORKERS`).
- `backend/backtesting/strategy_backtest.py` — `BACKTEST_FETCH_WORKERS=12`, passes `max_workers` through, and accepts `strategy_params` overrides.
- `backend/trading/strategies/stable.py` / `aggressive.py` — `__init__(**overrides)` copies snake_case override kwargs onto class attrs (uppercased) for per-instance tuning.
- `backend/trading/strategies/registry.py` — `get_strategy()` builds a fresh strategy instance when overrides are passed, leaving singletons untouched; `HybridStrategy` routes overrides to its two sub-strategies.

#### Key finding — universe size
- `AGENTS.md`/todo calls it a "44-stock universe," but `backend/utils/constants.py:63` `STOCK_UNIVERSE` currently contains **74 tickers** (this is unchanged from HEAD — the 44-count reference is stale, not a regression). Backtests therefore use 74 symbols unless tickers are passed explicitly.

#### Baseline full-universe backtest (2023-08-13 → 2026-08-13, 3y)
Ran with 74-symbol panel, daily frequency, 10 bps costs, 5% fixed slice per position, max 10 positions, $100k capital.

| strategy | trades | total return | win rate | profit factor | Sharpe | max DD |
|---|---|---|---|---|---|---|
| stable (defaults) | 180 | +9.59% | 60.0% | 1.725 | 1.06 | 5.26% |
| aggressive (defaults) | 142 | +6.03% | 40.8% | 1.345 | 0.58 | 3.25% |

Both strategies are net positive on the full universe (vs the prior 5-stock smoke test where aggressive was -0.76%).

#### Correctness gaps to fix before tuning (found during review)
1. **Fill timing:** entries fill at the signal-day close (`strategy_backtest.py:238` `entry_price: signal.entry_price`), but the engine and `ShadowTradingEngine` document next-bar-open fills (`engine.py` module docstring, `shadow.py:44`). Align on next-bar open ± slippage so results match the documented methodology.
2. **Stable time-stop:** `stable.py` documents exit rule D "close after 15 trading days," and `MAX_HOLD_DAYS=15` exists, but `check_exit()` never implements it; the backtest also tracks no holding-day counter. Either implement it or remove the stale doc + constant.

#### Stop-loss / R:R decision (user question)
- Current `STABLE_LOSS` 5% with BB-mid (SMA20) target is the default; user questioned whether 3% is too tight and whether to use a stronger rule or an R:R (3R) rule instead.
- Pending decision — see discussion below; not implemented yet. The 3% stop is too tight for the BB mean-reversion entry (whipsaw risk); leaning: keep a slightly larger initial stop and/or add an explicit 1:3 R:R toggle so target is derived from stop (`target = entry + 3 × (entry − stop)`) when reward would otherwise be capped by BB-mid.
- Aggressive uses an 8% trailing stop; a R:R / wider trailing stop may matter more there.

#### VPS research snapshot (July 2026 pricing from vendor sites)
- **DigitalOcean** Basic: 4 GiB / 2 vCPU = $24/mo; 8 GiB / 4 vCPU = $48/mo (per-second billing, monthly cap). 1 vCPU/1 GiB = $6/mo.
- **Vultr** Cloud Compute Regular: 1 vCPU/1 GiB = $5/mo; 4 GiB/4 vCPU = $40/mo; High Performance 2 vCPU/2 GiB = $18/mo. VX1: 2 vCPU/8 GiB = $0.060/hr ≈ $43/mo.
- **Hetzner** Cloud: entry Regular Performance shared plans from ~€4-5/mo (earlier logs referenced ~HK$34–60/mo for 4GB); not yet confirmed concrete SKU prices for 4GB in current page scrape.
- **Context from earlier logs:** DigitalOcean 4GB ≈ HK$188/mo was the up-to-now baseline; Hetzner 4GB ≈ HK$34–60/mo, so Hetzner or Vultr High-Performance 2GB are the main cheaper-with-similar-power candidates. Final choice pending (todo item below).

#### Next steps
- [ ] Fix fill timing to next-bar open + slippage (matches documented methodology).
- [ ] Implement or drop the Stable MAX_HOLD_DAYS time-stop (doc says 15 days).
- [ ] Tune Stable/Aggressive defaults with evidence from the baseline run; revisit stop vs 3R rule per user decision.
- [ ] Re-run full-universe backtests with corrected engine + tuned params; record results (replace/add to this log).
- [ ] Run full pytest suite; commit intended changes.

#### User notes (2026-08-13, recorded only — no code change)
- **Stop-loss / R:R:** User asked whether 3% stop is too tight and whether to make a slight change or adopt a 3R (risk-reward) rule. Review finding: 3% is too tight for the BB-lower mean-reversion entry (whipsaw risk) and, with the target capped at BB-mid, shrinks R:R toward 1:1. Candidates under discussion: (a) 3R rule — `target = entry + 3×(entry−stop)` overriding the BB-mid cap, with slightly wider stops; (b) slight fixed-% nudges only; (c) ATR-based stop with BB-mid target. **Decision: hold — just taking notes, no code change yet.** Revisit before the tuned re-run.
- **World monitor:** User asked to review an external resource called "world monitor" for quant-trading insight. It is not present anywhere in this repo and no URL was provided, so it was not guessed. **Decision: hold — notes only; awaiting the actual source/URL before any review.**

#### VPS 研究对比（2026-08-13 完成）

**当前基准：** DigitalOcean 4GB ≈ HK$188/月

##### 候选方案对比表

| 提供商 | 配置 | 价格/月 | 特点 | 数据中心 |
|---|---|---|---|---|
| **Hetzner CPX21** ⭐ | 3 vCPU / 4GB / 80GB NVMe | €4.51 ≈ **HK$38** | 共享 vCPU，ISO 27001 | 德国/芬兰/美国/新加坡 |
| Hetzner CPX31 | 4 vCPU / 8GB / 160GB NVMe | €8.93 ≈ HK$75 | 更大内存 | 同上 |
| Hetzner CCX13 | 2 专用 vCPU / 8GB / 80GB NVMe | €14.17 ≈ HK$119 | 专用 CPU，高性能 | 同上 |
| Vultr Regular | 2 vCPU / 4GB / 80GB SSD | $20 ≈ HK$156 | 共享 vCPU | 全球 33+ |
| Vultr High Perf | 2 vCPU / 4GB / 100GB NVMe | $24 ≈ HK$187 | AMD/Intel 新一代 | 全球 33+ |
| DigitalOcean Basic | 2 vCPU / 4GB / 80GB SSD | $24 ≈ HK$187 | 按秒计费 | 全球多地 |
| DigitalOcean Basic | 4 vCPU / 8GB / 160GB SSD | $48 ≈ HK$374 | 扩容选项 | 全球多地 |

##### 推荐方案：Hetzner CPX21

**理由：**
1. **成本节省 80%**：HK$188 → HK$38/月，年省约 HK$1,800
2. **配置充足**：3 vCPU / 4GB RAM 足以运行 TradingWorker + Streamlit + 轻量数据库
3. **稳定性保证**：自有数据中心，ISO 27001 认证，NVMe SSD，99.9% SLA
4. **数据中心选择**：
   - 欧洲（推荐）：芬兰 Helsinki（GDPR 合规，绿色能源）
   - 美国：Hillsboro (OR) 或 Ashburn (VA)
   - 亚洲：新加坡（延迟最低，价格略高 ~10%）
5. **迁移简单**：Ubuntu 22.04 LTS 镜像与 DigitalOcean 完全兼容

**备选方案：**
- 如需专用 CPU（高频交易/大并发）：Hetzner CCX13，€14.17/月，仍比 DO 便宜 37%
- 如需保持 DO 生态/熟悉度：维持现状，但成本高 5 倍

**决策：** 推荐采用 Hetzner CPX21。迁移步骤已记录在 UPGRADE_LOG Session 2026-08-12 的 VPS 部署待办中。
