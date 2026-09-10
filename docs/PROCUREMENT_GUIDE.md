# ALPHA//DESK 付費服務與 VPS 採購指南

Last updated: 2026-09-10

依現行架構（Alpaca 下單 + Streamlit 研究端 + Worker 常駐）與
[`PAPER_VALIDATION_RUNBOOK.md`](PAPER_VALIDATION_RUNBOOK.md)、
[`../deploy/README.md`](../deploy/README.md)、
[`PAID_DATA_PIT_PLAN.md`](PAID_DATA_PIT_PLAN.md)、
[`ARCHITECTURE_DECISION.md`](ARCHITECTURE_DECISION.md) 整理。

**價錢會變，以下為量級；下單前以官網為準。**  
金鑰只放本機或 VPS 的 `.env`，**不要**貼聊天、PR 或進 git。

## 總原則

1. **先驗證、再加碼**：Shadow／紙上階段不必買齊實時股票＋實時期權。
2. **不要買**淘寶轉賣／共享 API Key 當正式行情來源。
3. 「不差錢」也不代表一次開雙 Advanced（約 $400／月量級）；先把 **下單路徑 + 常駐 Worker + 可靠日 K** 跑穩，再加 SIP／OPRA。
4. 對齊 ADR-001：保留研究 UI，錢花在執行與數據，不花在重寫前端。

```text
Phase1 Shadow/Paper  →  Phase2 可靠日K  →  Phase3 小資金實盤
   Alpaca Paper           Polygon 股票日K      Live + 可選實時SIP
   Hetzner VPS            （非 Advanced 先）    可選實時期權
   Telegram / LLM可選
```

---

## Phase 1 — 現在就該有（Shadow／紙上 30–90 天）

| 項目 | 建議 | 約略成本 | 用途 | `.env` |
|------|------|----------|------|--------|
| **券商 API** | **Alpaca**（Paper） | **$0** 交易；Paper 免費 | 已接好的下單通道；`APCA_PAPER=true` | `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` / `APCA_PAPER=true` |
| **VPS** | **Hetzner CPX21**（3 vCPU / 4GB / ~80GB）Ubuntu 24.04 | 約 **€5–8／月** | 24/5 跑 trading worker | 機器上 `/opt/stock-analyzer/.env` |
| **行情（最低）** | Yahoo（已內建） | **$0** | Shadow／技術分析 fallback | 無 |
| **PIT 宇宙** | [`data/historical_universe.json`](../data/historical_universe.json) | **$0** | 減少倖存者偏差 | 無 |
| **通知** | Telegram Bot | **$0** | 成交／急停／日 P&L | `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` |
| **LLM** | 既有 OpenAI-compatible | 依用量 | 研究輔助；**不可繞過風控** | `LLM_API_KEY` / `LLM_BASE_URL` |

**Phase 1 合計：約一個小 VPS 月費 +（可選）LLM。**  
此階段**不必**買 Polygon Advanced、IBKR、QuantConnect、Bloomberg。

開通步驟摘要：

1. 在 [Alpaca](https://alpaca.markets/) 開 Paper，產生 API Key → 寫入 `.env`。
2. 在 [Hetzner Cloud](https://www.hetzner.com/cloud) 開 **CPX21**（或同級 4GB），依 [`deploy/README.md`](../deploy/README.md) 部署 systemd worker（先 `--mode shadow`）。
3. 用 [@BotFather](https://t.me/BotFather) 建 Bot，把 token／chat id 寫入 `.env`。
4. LLM 維持現有供應商即可。

---

## Phase 2 — 封印前加購（可靠日 K／少依賴 Yahoo）

對齊 [`polygon_equity.py`](../backend/agents/polygon_equity.py) 與 [`PAID_DATA_PIT_PLAN.md`](PAID_DATA_PIT_PLAN.md)。

| 項目 | 建議 | 約略成本 | 何時買 |
|------|------|----------|--------|
| **美股聚合日 K** | **Polygon／Massive Stocks** | 入門／Developer 約 **$29–$79／月**（多為**延遲**；以官網為準） | 開始認真多月 shadow／paper、要穩定 OHLCV |
| **不要先買** | Stocks **Advanced 實時 SIP**（約 **$199／月** 量級） | 貴 | 僅盤中秒級需求 + 已過紙上閘門 |

說明：

- 本專案自動交易主路徑偏**日線／次日開盤**，**延遲日 K 通常夠 Phase 2**。
- 標 15 分鐘延遲可用於研究與日頻；**不要**當成可執行盤中 NBBO。
- 金鑰：`POLYGON_API_KEY`（可選 `POLYGON_BASE_URL`）。

重建宇宙（可選）：

```bash
PYTHONPATH=backend:. python3 scripts/build_historical_universe.py --use-polygon
```

---

## Phase 3 — 小資金實盤前／後才考慮

僅在 [`PAPER_VALIDATION_RUNBOOK.md`](PAPER_VALIDATION_RUNBOOK.md) 簽核後：

| 項目 | 建議 | 約略成本 | 備註 |
|------|------|----------|------|
| **Alpaca Live** | 同帳戶切 `APCA_PAPER=false` + `--allow-live` | 美股常 $0 佣金；或有行情費 | **小資金 + mandate 縮額**；先人工核准 |
| **實時股票（可選）** | Alpaca 付費行情 **或** Polygon Stocks Advanced | 約 **$99–$199／月** | **二選一**，勿重複付兩份 SIP |
| **實時期權（可選）** | MarketData Trader 約 **$75／月**，或 Polygon Options Advanced 更貴 | 僅 actionable 期權警報 | 自動交易主線**不做期權自動下單**；可晚買 |
| **IBKR** | **暫緩** | 帳戶＋行情 | 程式尚未接 IBKR |
| **QuantConnect／Lean** | **暫緩** | 約 $20–$200+／月 | 嚴謹回測外掛；非現階段必買 |
| **Postgres 託管** | **暫緩** | 約 $5–15／月 | 預設 **SQLite** 已夠個人 VPS |
| **Bloomberg／Refinitiv** | **不要買** | 企業年費級 | 個人過殺 |

---

## 現在買／先別買

### 現在就買／開通

1. Alpaca Paper API Key（免費）→ `.env`
2. Hetzner CPX21（或同級）VPS → [`deploy/README.md`](../deploy/README.md)
3. Telegram Bot（免費）→ 運維通知
4. （研究端需要）維持現有 LLM 額度

### 1–2 週內、認真封印時再買

5. Polygon／Massive **股票**方案（**非** Advanced 可先試）→ `POLYGON_API_KEY`

### 通過紙上 ≥30 天 + Stage 2 seal 之後

6. Alpaca Live +（可選）實時股票套餐  
7. 實時期權 — **僅期權警報需求**  
8. 更大 VPS／Postgres／IBKR／QC — **有痛點再升級**

### 不要買

- 淘寶／不明轉賣「Polygon 共享 Key」
- 起步就開股票 Advanced **加** 期權 Advanced（約 $400／月量級）
- Bloomberg、雙券商並行、FinBERT 專用 GPU 機（非現階段）

---

## 建議月費預算帶

| 階段 | 合理月費量級 | 包含 |
|------|--------------|------|
| Shadow／Paper 起步 | **約 $5–30** | VPS +（可選）延遲股票 API + LLM 輕量 |
| 認真封印 | **約 $30–100** | VPS + Polygon 股票日 K + LLM |
| 小資金實盤 | **約 $50–200** | 上列 +（可選）實時股票；期權另計 |
| 完整實時股＋期權 | **約 $200–400+** | 僅明確需要盤中＋期權警報時 |

---

## `.env` 對應速查

放在**專案根目錄 `.env`** 或 VPS `/opt/stock-analyzer/.env`（`chmod 600`）。範本：[`.env.example`](../.env.example)。

| 階段 | 變數 |
|------|------|
| Phase 1 必備 | `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `APCA_PAPER=true` |
| Phase 1 建議 | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Phase 1 可選 | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, … |
| Phase 2 建議 | `POLYGON_API_KEY`（可選 `POLYGON_BASE_URL`） |
| 研究可選 | `ALPHA_VANTAGE_API_KEY`, `TRADIER_*`, `MARKETDATA_*` |
| 帳本 | `ORDER_STORE_BACKEND=sqlite`（預設）；Postgres 僅 Phase 3+ 有需要時 |

---

## 一句話決策

**先開 Alpaca Paper + 一台 Hetzner 小 VPS + Telegram；封印前再加 Polygon 股票日 K；實盤通過紙上閘門後再考慮實時 SIP／期權。其餘先別買。**
