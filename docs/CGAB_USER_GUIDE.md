# Cgab — new user guide / 新手教學

Cgab is a US-equity research desk with **Alpaca paper** trading. The live UI is **http://127.0.0.1:8000/**.

Cgab 是美股研究工作台，交易預設走 **Alpaca 模擬盤（paper）**。畫面網址：**http://127.0.0.1:8000/**

---

## English

### 1. Start the app

From the repo folder:

```bat
.\.venv\Scripts\python.exe -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ — you should see a **PAPER** badge. That means orders go to Alpaca paper, not live cash.

First visit: a short tour appears. Replay it from **More → Guide** or the **?** button. Switch language with **EN / 繁** in the top bar.

### 2. Sidebar groups

| Group | What it is |
|---|---|
| **Navigation** | Daily workspace: Desk (holdings), Orders, Research, Market, Chart |
| **Apps** | Tools: AI Mode (tune + start auto), Strategies, Alerts, Calendar |
| **More** | Journal, Options, Settings, Guide |

### 3. Desk vs AI Mode

- **Desk** shows the real Alpaca paper book: equity, cash, positions, manual ticket.
- **AI Mode** is the only place to pick the strategy and **Start / Stop paper auto**.
- Saving sliders does **not** start the bot. You must press **Start paper auto**.

The top bar **AUTO ON** means the worker process is alive and its heartbeat is fresh. **AUTO OFF** means it is not scanning.

### 4. How paper auto trading works

1. `.env` has `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, and `APCA_PAPER=true`.
2. Kill switch on Desk must be **OFF** (Resume) or the bot will not open new buys.
3. AI Mode → strategy (start with **stable**) → **Start paper auto**.
4. The worker polls about once a minute. It fetches prices, scores names, then sizes with Kelly and RiskEngine.
5. Fills appear on **Desk → Holdings** and **Orders**. Empty holdings = no fill yet.

US regular hours are about **09:30–16:00 ET**. Outside that window the worker stays alive but **does not scan**, unless `.env` has `IGNORE_MARKET_HOURS=true`.

### 5. Why paper trading may look “not working”

This is expected until a signal actually fills:

- Auto was never started (AUTO OFF).
- It is weekend / outside US cash session.
- Kill switch is on.
- No name passed the entry threshold (default ~70) plus risk gates.
- Yahoo / data fetch failed for that cycle (check `backend/data/worker.log`).
- You are looking at Desk holdings — cash can stay ~$100k with **0 positions** for a long time.

**Prove the paper account itself works:** on Desk, Buy 1 share of a liquid name (e.g. AAPL) with a market order. If that fill shows, brokerage is fine and only the *strategy* has not fired.

### 6. Safety

Live trading is blocked in this UI. Do not set `APCA_PAPER=false` until a paper runbook is signed.

---

## 繁體中文

### 1. 啟動應用

在專案資料夾執行：

```bat
.\.venv\Scripts\python.exe -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

打開 http://127.0.0.1:8000/ ，頂欄應有 **PAPER**。代表下單走 Alpaca 模擬盤，不是真錢。

第一次進入會出現導覽。之後可到 **更多 → 教學** 或按 **?** 重看。語言用頂欄 **EN / 繁** 切換。

### 2. 側欄分組

| 分組 | 用途 |
|---|---|
| **導覽** | 日常工作區：工作台（持倉）、訂單、研究、行情、圖表 |
| **應用** | 工具：AI 模式（調參並啟動自動）、策略、警示、行事曆 |
| **更多** | 日誌、選擇權、設定、教學 |

### 3. 工作台與 AI 模式

- **工作台**顯示 Alpaca 模擬帳戶的真實帳本：權益、現金、持倉、手動下單。
- **AI 模式**才是選擇策略並 **啟動 / 停止紙上自動交易** 的地方。
- 只儲存參數 **不會** 啟動機器人，必須再按啟動。

頂欄 **AUTO ON** 代表工作行程活著且心跳正常。**AUTO OFF** 代表沒有在掃描。

### 4. 紙上自動交易怎麼開

1. `.env` 要有 Alpaca 金鑰，且 `APCA_PAPER=true`。
2. 工作台上的緊急停止必須是關閉（Resume），否則不會開新倉。
3. AI 模式 → 選策略（建議先用 **stable**）→ **啟動紙上自動交易**。
4. 行程約每分鐘掃描一次：抓行情、評分、凱利倉位、風控，通過才下單。
5. 成交後出現在 **工作台持倉** 與 **訂單**。空白 = 還沒成交。

美股正規交易約 **美東 09:30–16:00**。盤外行程仍在，但 **不掃描**，除非 `.env` 設 `IGNORE_MARKET_HOURS=true`。

### 5. 為什麼看起來「沒在交易」

在真正成交前，這些都正常：

- 尚未啟動自動（AUTO OFF）。
- 週末或美股盤外。
- 緊急停止開啟。
- 沒有標的通過進場門檻與風控。
- 該輪 Yahoo 行情失敗（看 `backend/data/worker.log`）。
- 帳戶可能長期維持約 10 萬美元現金、**0 檔持倉**。

**先確認券商模擬盤可用：** 在工作台市價買 1 股流動性高的標的（例如 AAPL）。若有成交，代表券商正常，只是策略尚未發出訊號。

### 6. 安全

此介面封鎖真實下單。在紙上驗證手冊簽署前，不要把 `APCA_PAPER` 設成 `false`。
