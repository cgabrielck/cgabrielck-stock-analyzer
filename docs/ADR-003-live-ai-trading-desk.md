# ADR-003 — Live AI Trading Desk（線上 AI 交易台）

**Status:** Accepted — accepted by Gabriel in chat 2026-10-21  
**Date:** 2026-10-21  
**Accepted:** 2026-10-21 by Gabriel (chat)  
**Decision ID:** ADR-003  
**Language:** 中文為主 / English headings OK  

> **Note on ADR-002:** ADR-002 is **VOID** by owner decision dated **2026-10-21**. It must not be cited as authoritative. Any overlap with older text below is coincidence of engineering preference, not inheritance of that ADR.

---

## 1. Context｜背景

Owner 北極星已定稿（2026-10-21 PM session）：

**做出一個可遠端使用嘅線上交易台：** 用戶可以像 TradingView 咁睇圖、用指標；每筆交易日誌可追溯、用戶自己睇到；App 提供**參考交易訊號**（建議買入／賣出、指標解讀、策略——細節仍規劃中），並用 AI 幫管戶同輔助決策（美股／期權，之後可加黃金）。體驗似迷你富途但更易讀。**唔係**淨係後台 Python 指標工具。真倉要過證據閘；Code Review 擋住 hardcoded 結論同假 source／假 summary 畀其他 worker。

痛點：現有交易邏輯偏「按步驟」、難幫到賺錢；目標係有界智能（硬風控之上做判斷），而唔係再用一套指標玩具替代產品。

建設支柱（Build pillars）：

1. **VPS** — 遠端可跑、可監控嘅虛擬主機  
2. **Market data API** — 股票／期權輸入源；之後可考慮黃金  
3. **User-facing App** — 可遠端操作 console，圖表＋指標＋策略＋AI bot，易讀易用  
4. **Code Review 紀律** — 自動／半自動擋 hardcoded、假 summary、假 source；debug 同更新可控  

---

## 2. Decision｜決策（編號）

### D1. Hard rules / soft judgment｜規則硬、判斷軟

- **硬規則（必須留喺確定性程式碼）：** 止損、曝險上限、日虧／熔斷類限制、kill switch、mandate、以及 **position sizing**（由權益 × 風險％ ÷ stop 距離等公式算出）。  
- **AI 唔准：** 傳 raw size、繞過 risk／mandate／kill、單獨因新聞／headline **發起**入場。  
- **AI 准做：** 在規則判定 setup 為 VALID 之後（或為參考訊號卡），套用**閉合清單 veto**＋可選 **conviction**，而 conviction 只可選 sizer **已經允許**嘅倉位檔位（tier）。News／headlines：**只否決，唔觸發**。

### D2. Intelligence success metric｜智能層成功標準

- Shadow／paper 上，加入 AI 層後嘅 **expectancy（R-multiple）** 必須優於**同一 universe** 下「純規則」對照組，否則 **AI 層唔晉升**（唔上 live 產品路徑）。  
- 賺錢論述必須可寫進驗收：\(E = (勝率 × 平均贏 R) - (敗率 × 平均輸 R)\)；細樣本唔當定論。

### D3. Veto safety｜否決安全

- 只用**閉合 veto 原因碼**＋寫入 audit。  
- 每筆否決必須記 **counterfactual「若無 veto」** 計劃結果，用 shadow follow-up 校準。  
- **禁止**開放式「用常識／判斷」否決。  
- 若某原因碼拖低對照組 E，可降級為 **只降倉、唔否決**，或關閉該碼。

### D4. Live promotion｜真倉晉升

- Paper／shadow **證明唔到**「永遠賺錢」；只證明**晉升閘已過**。  
- 可複用／改編現有 runbook 思路（**唔當舊 ADR 為聖旨**）：多日、**只有 filled 計數**；seal 產物；風控曾被真實觸發演練；人為簽核；先小資金。  
- **明文：未成交 limit ≠ 證據。**

### D5. Codebase stance｜代碼庫立場（工程偏好，非舊 ADR 繼承）

- **優先**喺現有 `backend/trading/` 内核（risk、safety、engine、strategies 等）上演進，**勝過** greenfield 整庫重寫——**除非**日後 Owner 明確決定重寫。  
- 新工作焦點：遠端 desk UX（圖表、指標、可追溯日誌、參考訊號）、VPS 部署、行情 API、advisory／veto 層、Code Review 閘。

### D6. Build pillars｜建設支柱

VPS；市場數據 API（股／期權，後續黃金）；用戶面 App；Code Review 紀律——四者並行排期，但 **真倉路徑唔可以跳過 D2／D4**。

### D7. Reference signals｜參考訊號（規劃中）

- 確切 schema **仍標「still planning」**。  
- 必須：用戶可見、可解釋、寫入日誌，且帶 **Code Review 可核實嘅 sources**（禁假 source／假 summary）。

---

## 3. Consequences｜後果

**正面**

- 產品形態對齊「迷你富途＋TradingView 可讀性＋可追溯日誌」，而唔係純後端腳本。  
- AI 有明確 remit：veto／conviction／解讀；風控語意可審計、可單測。  
- 晉升同智能層都有可否決嘅數字閘，減少「靠感覺上真倉」。

**代價／風險**

- Desk UX＋圖表＋日誌追溯係顯著前端／產品工作量。  
- Veto 假陰性（錯過好盤）要用 shadow 儀器管理，唔係一次寫死。  
- 演進内核仍要嚴格 Code Review，避免「為咗似 AI」而打開 size／bypass 後門。  
- 參考訊號 schema 未定，早期 UI 要用「規劃中」標記，避免假裝已有完整策略產品。

---

## 4. Acceptance criteria｜驗收清單（A–D）

- [ ] **A — 智能／賺錢：** AI 層有 shadow／paper **R-expectancy 對照**（有 AI vs 純規則，同 universe）；E ≤ 0 或不優於對照 → 唔晉升 AI 層。  
- [ ] **B — Veto：** 僅閉合原因碼；每筆有 audit＋「若無 veto」；開放式判斷 veto = 拒收；有校準／降級路徑。  
- [ ] **C — 真倉：** 晉升清單寫死（filled-only、seal、風控演練、人簽、小倉先行）；未成交 limit 唔算數。  
- [ ] **D — 工程邊界：** 新 PR 默認演進 `backend/trading/`；greenfield 重寫要 Owner 另決；Code Review 擋 hardcoded 結論／假 source／假 summary。  
- [ ] **產品面：** 遠端可睇圖＋指標；交易日誌用戶可見可追溯；參考訊號標 planning 但已有可見／可解釋／可溯源最小形狀。  
- [ ] **支柱：** VPS、行情 API、App、Code Review 閘喺路線圖有明確工單，且唔繞過上述閘。

---

## 5. Out of scope｜範圍外（本 ADR 唔批准）

- 完整參考訊號／策略 schema 定稿（仍 planning）。  
- 黃金／商品交易實作細節（只預留「之後可加」）。  
- 多 Agent 辯論委員會做主執行引擎。  
- LLM／聊天渠道直接下單。  
- 保證盈利或「永遠可上真倉」嘅承諾。  
- 以舊 ADR 文本作為約束來源。

---

## 6. References｜參考（外部；唔引用 ADR-002 作權威）

- Van Tharp Institute — trading system components, R-multiples, position sizing.  
- Anthropic Engineering — *Writing effective tools for agents*（工具 affordance；減少錯誤路徑）.  
- SEC — investor materials on automated trading risks；完整績效軌跡 vs 精選戰績.  
- Iron Mind (2026) — autonomous LLM trading agent architecture（模型外風控、工具唔暴露 raw size 等模式）.  
- HKUDS Vibe-Trading — mandate／kill（fail-closed）vs advisory（可觀測、唔削弱硬閘）模式討論.

---

## 7. North star（鎖定原文精神｜Locked）

Remote trading desk: TradingView-style charts/indicators; every trade log user-visible and traceable; app provides reference signals (buy/sell suggestions, indicator interpretation, strategy — details still planning); AI helps manage the trading account (US stocks/options, later possibly gold); mini-Futu-like but easier to read; **NOT** a Python-only backend indicator tool. Live trading requires evidence gates. Code Review must block hardcoded conclusions and fake sources/summaries for other workers.

---

## Addendum A（2026-10-21）— Proposal 治理式自我升級｜Accepted

**Status:** **Accepted** by Gabriel（chat／owner decision 2026-10-21）  
**Does not revive ADR-002.** ADR-002 remains VOID.

### A.1 決策摘要

Harness／AI **可以**自我升級，但必須走用戶治理路徑：

1. **Learn + digest**（含失敗交易／journal）。  
2. 產出 **完整最新更新版本**，打包為 **Proposal**（config／策略／規則 diff ＋ rationale ＋ 失敗／指標證據）。  
3. **向用戶呈現／說服**，等待明確批准。  
4. **僅在明確 Approve 之後**先 merge 入運行中 harness／App config。  
5. 目的：**更好嘅用戶治理**——**唔係**靜默 full-access 自動改寫憲法。

### A.2 硬規則（本 Addendum **唔削弱**原 D1）

- Runtime：**AI 仍然唔准**傳 raw size、唔准繞過 M8（gates／sizer／mandate／kill）。  
- **學習唔准靜默改 L2**：mandate、kill、風險上限（風險％）、live 旗標、強制止損憲法 —— 只可經 Approved Proposal merge。  
- **L1（可自動）：** intents、conviction、watchlist、閉合清單內 veto 開關等 runtime 意圖層。  
- **L2（憲法）：** 只經 Proposal → Approve → Merge（可 Rollback）。

### A.3 雙時鐘（同捆鎖定）

- **H1** 實時 loop（出場／kill／保護）**唔准**阻塞等 LLM。  
- **H3** 決策週期寫 **IntentStore**；harness 消費意圖先落單。  
- **Proposal** 係與 live Intent **分離**嘅治理路徑。

### A.4 驗收加項

- [ ] 至少一條端到端：**digest → Proposal vN → 用戶 Approve／Reject → Merge／Rollback**。  
- [ ] 未 Approve 時 L2 檔案／限額語意不變。  
- [ ] Proposal 無 sources／假 summary ⇒ Code Review（M13）拒收。  
- [ ] H1 路徑無同步 LLM 呼叫（測試或架構檢查）。

### A.5 詳述文件

見 [`MODULAR_ARCHITECTURE.md`](MODULAR_ARCHITECTURE.md) **§4.4**（雙時鐘）、**§4.5**（Proposal 治理）、**§5.5**（`HarnessProposal` 合約）。

*Addendum A locked 2026-10-21 from owner decision on proposal-governed self-upgrade.*


*ADR-003 drafted 2026-10-21 from PM session. Accepted by Gabriel in chat on 2026-10-21. Addendum A accepted 2026-10-21.*
