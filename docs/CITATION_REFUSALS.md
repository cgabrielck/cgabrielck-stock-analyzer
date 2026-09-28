# Citation refusals（主路徑禁運）

兩條分支（`main` 同 `lab/citation-try`）都遵守：

## 永不入交易主路徑

| 項目 | 原因 |
|------|------|
| FinRL／PPO／端到端 RL 下單 | 難審計；同 RiskEngine／mandate 衝突 |
| CrewAI 當生產編排 | 輸畀可審計狀態圖；易繞風控 |
| TradingAgents **整庫替換** | 丟掉 Kelly／kill／Alpaca desk（可偷角色，見 lab advisory） |
| HFT／做市／一鍵 AI 炒股 repo | 基建同產品邊界唔符 |
| 共享／來路不明行情 Key | 封印作廢 |

## 本實驗仍唔一次裝齊

OpenBB 全家、LEAN 宿主、Qlib、FinGPT、TA-Lib、PyPortfolioOpt、第二套 Pydantic AI runtime。  
封印／研究 SKU 需要時另開 slice；唔好同 lab weekend A/B 捆綁。

## 官方次序（唔跟他庫「Instructor→LangGraph→FinBERT」當交易升級）

`STRATEGY_ADOPTION` §4：P0–P2（已做）→ **P3 日 K＋shadow 封印** → P4 → P5 FinBERT（可選軟門）。  
Lab 只驗證結構化／情緒／報告工具，**唔取代** P3 日曆證據。
