# ALPHA//DESK — 可引用項目推介名單（Citation Candidates）

**Last updated:** 2026-09-12  
**Scope:** 只做推介／寫入名單，**唔做任何 build／依賴安裝／改碼**。  
**篩選條件（必須同時滿足意向）：**  
1. **可顯著提升「模型／訊號／證據」表現**（預測品質、風險校準、回測可信度、情緒／基本面特徵）  
2. **可顯著提升 UI ↔ 多 Agent 協作與最終推薦**（結構化交接、辯論／覆核、可觀測性、報告撕裂圖、研究終端一致體驗）  

**硬約束（見 [`STRATEGY_ADOPTION.md`](STRATEGY_ADOPTION.md)）：**  
long-only 規則殼優先；LLM／情緒只可軟門；禁止把端到端 RL／HFT／做市／未封印黑盒當主路徑。

**現有已用（唔重複推）：** yfinance、Streamlit／FastAPI desk、VADER、Pydantic、Alpaca、OpenAI-compatible LLM、自研 RiskEngine／Kelly／shadow。

---

## S 級（優先引用 — 雙條件都強）

| # | 項目 | 可靠度 | 主要引用方式（概念／介面，非 build） | 條件① 模型／訊號 | 條件② UI／Agent 協作／最終推薦 |
|---|------|--------|--------------------------------------|------------------|--------------------------------|
| S1 | **[LangGraph](https://github.com/langchain-ai/langgraph)** | 高（生產級編排） | 把 Fundamental／Technical／Sentiment／Risk／Trader 做成**有狀態圖**；節點輸出寫入共享 `AgentState` | 強制「分析→辯論→風控→推薦」順序，減少單 Agent 幻覺下單 | UI 可逐步展示節點狀態、checkpoint、人機審批閘；最終推薦帶完整 provenance |
| S2 | **[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)**（論文框架） | 中高（研究框架，非券商級） | **引用角色設計與辯論協議**（Bull/Bear、Risk Judge、結構化 `TradeRecommendation`），唔整庫替換你哋内核 | 多角色覆核可提升推薦穩健度與可解釋性 | Desk UI 可做「分析師對話時間線」；最終建議必經 Risk 節點先顯示 |
| S3 | **[Instructor](https://github.com/567-labs/instructor)** + 既有 **Pydantic** | 高 | 所有 Agent 輸出強制 schema（分數、置信度、否決理由、停損、RS、風險標籤） | 結構化輸出↓解析失敗／亂建議；可重試驗證 | Agent 之間 JSON 交接穩定；UI 卡片／徽章直接綁字段 |
| S4 | **[Pydantic AI](https://github.com/pydantic/pydantic-ai)** | 高（同生態） | 工具呼叫＋typed deps＋可觀測；適合「研究 job」長流程 | 工具結果可驗證，減少幻造數字 | 同 FastAPI desk／job API 對齊；失敗可重放；利於最終推薦審計 |
| S5 | **[ProsusAI/FinBERT](https://github.com/ProsusAI/finBERT)** 或 HF **finbert-tone** | 高（經典金融情緒） | **替換／增強 VADER** 做新聞標題／摘要三分類；只准 ±軟權重 | 金融域情緒明顯優於通用詞典 → 情緒門更準 | UI 顯示「FinBERT 概率條」＋與規則分並排；Agent 傳 `sent_pos/neg/neu` 而非散文 |
| S6 | **[QuantStats](https://github.com/ranaroussi/quantstats)** | 高（成熟 tear sheet） | Shadow／paper 績效 → HTML／圖表 tear sheet；對齊 seal 報告 | 統一 Sharpe／DD／月度熱圖，封印證據更可讀、可比 SPY | Desk「績效」頁一鍵報告；Agent 交接用同一套 metric 名 |
| S7 | **[OpenBB](https://github.com/OpenBB-finance/OpenBB)**（SDK／數據層優先） | 高（大型研究終端生態） | 作**多源數據／基本面／宏觀適配參考**；唔搬成第二個產品殼 | 數據覆蓋與標準化↑ → 特徵更穩 | UI／Agent 共用同一數據契約；減少各 Agent 各自 yfinance 各說各話 |

---

## A 級（強推但單邊偏重，或需克制用法）

| # | 項目 | 可靠度 | 引用重點 | ① | ② | 克制 |
|---|------|--------|----------|---|---|------|
| A1 | **[QuantConnect LEAN](https://github.com/QuantConnect/Lean)** | 極高 | 外部嚴格事件回測／成本模型宿主；你哋 Streamlit／desk **仍係 UI** | 回測可信度與機構對齊，提升「模型是否有效」判斷 | UI 展示 LEAN 報告摘要作最終推薦附件 | 唔好用 LEAN 取代 RiskEngine |
| A2 | **[Microsoft Qlib](https://github.com/microsoft/qlib)** | 高 | 特徵／截面研究流水線、因子實驗框架 | 系統化特徵與 walk-forward 實驗 | 研究 lab 結果餵 desk 對照卡 | **禁止**把 Qlib RL 範例直接接 Alpaca 主路徑 |
| A3 | **[PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt)** | 高 | 在 Kelly 帽內做 HRP／收縮協方差等**輔助**權重 | 相關結構更好 → 組合層表現 | UI 顯示權重來源（Kelly vs HRP） | 唔好改成無約束均值方差賭博 |
| A4 | **[Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib)** | 高 | CVaR／多風險度量有效前沿（研究頁） | 風險度量專業化 | 風控 Agent 輸出可對照圖 | 個人帳戶慎用過度優化 |
| A5 | **[TA-Lib](https://github.com/TA-Lib/ta-lib-python)**（或純 Python 指標庫如 **pandas-ta-classic**） | 高／中 | 指標計算標準化（RSI/MACD/ATR 等） | 指標一致性↑，同回測對齊 | 圖表／Agent 共用同一指標定義 | Windows 原生依賴安裝成本；可只引算法契約 |
| A6 | **[FinGPT](https://github.com/AI4Finance-Foundation/FinGPT)**（FinNLP／情緒 LoRA） | 中高 | 進階金融情緒／RAG **實驗軌** | 潛在優於通用 LLM 語氣 | 可做「模型對照」面板 | 重、要 GPU；**唔好**當唯一下單信號；先 FinBERT |
| A7 | **vectorbt／vectorbtpro 思路** 或輕量 **[Zipline 精神續作／自研 WF](https://github.com/stefan-jansen/zipline-reloaded)** | 中高 | 向量化參數掃描、快速敏感度 | 策略超參加速 OOS 篩選 | UI「參數熱圖」輔助最終揀參 | 注意授權（Pro）；開源向量化即可 |

---

## B 級（可觀察／靈感，暫唔建議深綁）

| 項目 | 點解降級 |
|------|----------|
| CrewAI / AutoGen | 快原型；金融審計同狀態機不如 LangGraph／Pydantic AI 硬 |
| FinRL / 各種 PPO 交易 repo | 違反採納紅線：端到端 RL 下單；最多學術對照 |
| 新晉 RaptorBT／Ziplime 等 | 有潛力但生態／審計軌跡短過 LEAN／自研引擎 |
| Trade Ideas / TrendSpider / Composer（商業） | 可作靈感；**唔可**當核心 alpha 依賴（閉源／不可審計） |
| Chronos／TimeGPT 類價測 | 預測≠交易系統；易過擬合敘事；僅研究標註 |

---

## 明確「唔好引用入主路徑」

| 項目類型 | 原因 |
|----------|------|
| HFT／LOB／做市開源棧 | 同 long-only desk 基礎設施唔匹配 |
| 未驗證「AI 自動炒股」一鍵 repo | 多數無風控硬閘、無 OOS 封印 |
| 共享／來路不明行情 Key 專案 | 證據鏈斷裂 |
| 以 LLM 直接輸出倉位且無 schema／RiskEngine | 同 ADR／STRATEGY_ADOPTION 衝突 |

---

## 建議引用次序（仍然只係名單，唔係施工）

1. **Instructor／Pydantic AI** — 立刻抬升 Agent 交接同最終推薦可信度（條件②最大）  
2. **LangGraph + TradingAgents 角色協議** — UI 時間線／辯論／風控必經（條件②＋①）  
3. **FinBERT** — 情緒層升級（條件①，軟權重）  
4. **QuantStats** — seal／paper 報告專業化（①＋②）  
5. **OpenBB 數據契約／LEAN 外部回測** — 證據層（①為主）  
6. **PyPortfolioOpt／Riskfolio（帽內）** — 組合層（①，UI 可解釋）  
7. FinGPT／Qlib — 實驗室對照，封印前唔入 live

---

## 同現有文件關係

- 採納紅線：[`STRATEGY_ADOPTION.md`](STRATEGY_ADOPTION.md)  
- 買咩服務：[`PROCUREMENT_GUIDE.md`](PROCUREMENT_GUIDE.md)  
- 本機交接：[`LOCAL_CURSOR_HANDOFF.md`](LOCAL_CURSOR_HANDOFF.md)  

**Agent 使用方式：** 只准按本名單「引用設計／接口／角色」；未另開 ADR 前**禁止**大規模依賴合併或替換 RiskEngine／Alpaca 路徑。

---

## 變更紀錄

| 日期 | 變更 |
|------|------|
| 2026-09-12 | 初版推介名單：S/A/B 級 + 禁止項；無 build |
