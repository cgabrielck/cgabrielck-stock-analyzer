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
