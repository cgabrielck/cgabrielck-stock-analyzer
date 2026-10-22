# Lab ≠ Product M7 綁定頁｜Lab Advisory Isolation Binding

> **Status:** Draft（plan-16）— 待 Owner／PM 審；**未 Accepted、未 merge 前唔作產品宣稱依據。Merge ≠ Accepted。**  
> **Binds under:** [ADR-003 + Addendum A](ADR-003-live-ai-trading-desk.md)（Accepted）> [MODULAR_ARCHITECTURE.md](MODULAR_ARCHITECTURE.md) > [AUDIT_REMEDIATION_PLAN_2026-10-21.md](AUDIT_REMEDIATION_PLAN_2026-10-21.md)（Confirmed items 1–5）> [BOUNDED_INTELLIGENCE_ACCEPTANCE.md](BOUNDED_INTELLIGENCE_ACCEPTANCE.md)（Draft：有界智能）> [ACCOUNT_MANAGEMENT_DESIGN.md](ACCOUNT_MANAGEMENT_DESIGN.md)（Draft：管戶範圍）。  
> **Index:** [ARCHITECTURE_DECISION.md](ARCHITECTURE_DECISION.md)。**ADR-002 is VOID.**  
> **North star（鎖定）：** 遠端交易台（圖表／指標／可追溯日誌／參考訊號 planning）；AI 幫管戶；硬風控之上先判斷；**真倉要過證據閘**；Code Review 擋假 source／假 summary／hardcoded 結論。  
> **Does not elevate:** Intent→M8／H1–H3、D7-DESIGN、Desk flatten product exception、fake verified／live、產品 M7 實作已交付。Issue #10 stays OPEN.  
> **Does not rewrite:** ADR-003 body；live Harness config／strategy／rules；L2 憲法只經 Proposal→Approve→Merge。  
> **Date:** 2026-10-23（Asia/Hong_Kong）  
> **Baseline SHA:** `67f2fddbe3c67f32728dcd943ad13f990c04dcfd`（`main`，含 plan-15 管戶設計頁）

---

## 1. 目的｜Purpose

用**一頁**畀 Code Review **可閘**嘅綁定：實驗室討論（`lab/advisory_debate` 或同等物）**永遠唔係**產品落單路徑（**M7 唔下單；M9／`OrderManager`／Desk submit 先係執行**）。

本頁係**隔離／接受頁**，唔係實作工單、唔係保證盈利、唔係「可上真倉」證書、唔係把 TradingAgents 辯論當已過風控訂單。

對齊：北極星（遠端 Desk，唔係淨後台 Python）；ADR-003 D1／D5（硬規則＋演進 `backend/trading/`）；MODULAR M7 **MUST NEVER**；AUDIT item 5 已寫 `lab/advisory_debate` ≠ 產品 M7。**ADR-002 is VOID**，唔引用作約束。

---

## 2. 係咩／唔係咩｜Definition

| 是（本頁綁定） | 不是 |
|----------------|------|
| **產品 M7**＝`backend/trading/advisory/`（TBD）：閉合 veto、conviction **tier**、解讀、digest→**Proposal**；**唔下單** | `backend/lab/advisory_debate.py`（或改名／搬位後仍係實驗室討論）當產品 M7 |
| Lab debate＝研究草稿：Bull／Bear／Risk memo、成本上限 LLM、A/B 指標、筆記 | Lab 結論當已核實 source、已過風控 Intent、或 `RISK_APPROVED` |
| Streamlit／`LAB_*` flags／`GET /api/lab/*`＝lab／legacy 研究面 | Lab API 或 Deep memo ＝ live Desk 執行 |
| Isolation fail-case 測（plan-13d）＝**擋接線** | Isolation 測綠＝產品 M7 已交付／可 live |

**接受陳述：** 實驗室可以討論；產品可以有界輔助（M7）；**討論唔准變成落單。**

---

## 3. 明確禁止｜Explicit forbid（Code Review 硬閘）

下列任何一項＝**拒收 PR**（M13）。唔接受「只係 research flag」「localhost」「paper 先試」「改名就當 M7」。

1. **禁止**將 `backend/lab/advisory_debate.py`、`lab/advisory_debate`、`run_advisory_debate`、或**同等實驗室討論**（委員會角色辯論、advisory graph／memo 產物）接到：
   - `OrderManager`／`submit_new_order`／任何 M9 落單入口  
   - broker adapter（Alpaca 或其他）／`submit_order`／live 或 paper **submit**  
   - Desk `place_desk_order`／`POST /api/orders`  
   - IntentStore writer（H3 `upsert_active_intent`）當已核實意圖  
2. **禁止**把 lab 輸出當成產品 M7：`VetoRecord`、閉合 veto 碼、conviction 最終檔、digest 已批准 Proposal。  
3. **禁止** lab 路徑蓋章 `RISK_APPROVED`／`verified`／`live`／filled-without-price。  
4. **禁止** LLM／Telegram／聊天／lab memo **直接下單**。  
5. **禁止** 用「升級 lab→產品」繞過 `backend/trading/advisory/` 同 Owner／PM Accepted。  
6. **禁止** 靜默改 L2（mandate／kill／風險％／live／策略預設）——Proposal→Approve→Merge 先算。

**同等物（or equivalent）：** 新檔、改名、`backend/lab/` 以外路徑，只要語意仍係「實驗室討論／辯論／研究 memo」，本禁令一樣適用。升格為產品 M7 必須：**新位置** `backend/trading/advisory/` ＋ 本頁 Accepted 之後另開產品工單；**唔係**把 lab 接上 `OrderManager`。

---

## 4. Lab 可以做咩｜What lab may do（non-execution）

Lab **只**做研究／筆記／非執行。允許（唔當產品交付）：

| 可以 | 說明 |
|------|------|
| 研究 memo | Bull／Bear／Risk 草稿；Desk Deep **research memo**（`product=True` 仍**永不**呼 `OrderManager`） |
| 成本上限 LLM | `LAB_ADVISORY` 等旗；預算／metrics JSON；worker **忽略**改 top-N 建議 |
| A/B 對照 | `scripts/run_lab_ab_compare.py`、lab 報告；**唔**用贏 SPY 當可 live |
| 非執行 API | `GET /api/lab/status`、`GET /api/lab/ab-report`、`POST /api/lab/advisory`、`POST /api/lab/tear`（memo／指標，**唔 submit**） |
| 引用實驗 | [`LAB_CITATION_TRY.md`](LAB_CITATION_TRY.md)；角色可偷，整庫 TradingAgents **唔**替換内核 |
| 筆記／隔離測 | plan-13d fail-case：lab **唔 import** 錢路徑；錢路徑 **唔**呼叫 `run_advisory_debate` |

Lab 結論**唔可**餵主路徑當已核實 source（見 [CITATION_REFUSALS.md](CITATION_REFUSALS.md)）。

---

## 5. 產品 M7 仍然係｜Product M7 remains（唔由本頁實作）

產品 M7 定義仍以 [MODULAR_ARCHITECTURE.md](MODULAR_ARCHITECTURE.md) M7 為準：

- Owns：`backend/trading/advisory/`（建議新建；**禁止**放喺可直呼 broker 之處）  
- 輸出：閉合 `veto_codes[]`、已允許 **conviction tier**、notes；`digest_for_proposal` → 未批 Proposal  
- **唔寫** orders；**唔直寫** L2；最終自動 qty **只由 M8**  
- H3 寫 Intent；H1 消費 Intent（**無**同步等 LLM）

有界智能接受條件見 Draft [`BOUNDED_INTELLIGENCE_ACCEPTANCE.md`](BOUNDED_INTELLIGENCE_ACCEPTANCE.md)。管戶範圍見 Draft [`ACCOUNT_MANAGEMENT_DESIGN.md`](ACCOUNT_MANAGEMENT_DESIGN.md)。**本頁唔實作、唔宣稱 M7 已交付。**

---

## 6. Code Review 閘｜Review checklist（合入前）

PR 若觸及 advisory／lab／M7／落單，審查員**必須**能勾：

- [ ] `backend/lab/advisory_debate.py`（或同等）**無** import／呼叫 `OrderManager`、`desk_orders`、`alpaca_broker`、`place_desk_order`。  
- [ ] `OrderManager`／`place_desk_order`／worker 錢路徑**無**呼叫 `run_advisory_debate`。  
- [ ] Lab 輸出**無**寫 IntentStore、**無** `RISK_APPROVED`／`verified`／`live` stamp。  
- [ ] 新「產品 advisory」落喺 `backend/trading/advisory/`，**唔**靠 lab 接線升級。  
- [ ] 本頁仍標 **Draft**；合入**唔**當 Owner／PM Accepted。  
- [ ] 無宣稱 live ready／產品 M7 已上線。

現有隔離測（`tests/lab/test_advisory_debate_isolation.py`，plan-13d）係 **fail-case 閘**，唔係產品完成證明。

---

## 7. 不得宣稱｜Must not claim

未 Accepted／未過對應閘之前，產品／文件／銷售／UI **禁止**宣稱或暗示：

1. **Lab advisory debate = 產品 M7**，或已接到 `OrderManager`／broker／live submit。  
2. **可上真倉／live ready／verified**（本頁唔 stamp；真倉仍要 ADR-003 證據閘）。  
3. **Isolation 測通過 = M7 已交付。**  
4. **Intent→M8／D7-DESIGN／Desk flatten／issue 10 庫存已完成。**  
5. **AI／LLM／Telegram／lab memo 可落單**或繞 M8。  
6. **本 Draft 合入＝Accepted 憲法**（Accepted 之後先升格；仍唔改 runtime）。  
7. **ADR-002 仍有效**（VOID）。

---

## 8. Draft vs still OPEN｜本頁合入後（docs only）

**Code 項唔因本頁變完成。** Status 同有界智能／管戶頁：**Draft** pending Owner／PM；**未 Accepted。Merge ≠ Accepted。** 未 Accepted、未 merge 前唔作產品宣稱依據。

| ID | Status | Notes |
|----|--------|-------|
| **本綁定頁** `docs/LAB_PRODUCT_M7_BINDING.md` | **Draft**（pending Owner／PM; **not yet Accepted**） | Merge ≠ Accepted；唔升格 PATH／M7 實作 |
| 有界智能接受頁 | Draft（plan-14） | veto／tier 條件；本頁專綁 lab 隔離 |
| 管戶設計頁 | Draft（plan-15） | Desk vs worker；本頁唔取代 |
| 產品 M7 `backend/trading/advisory/` | **OPEN（未交付）** | MODULAR TBD；唔由本頁實作 |
| lab `advisory_debate`→`OrderManager` | **OPEN（禁止去做）** | 隔離測已有；仍唔准接線 |
| Intent→M8／H1–H3／完整 M8 StrategyBase | **OPEN** | 唔由本頁排期 |
| D7-DESIGN | **OPEN** | 兩目錄未合併 |
| Desk flatten product exception | **OPEN** | 唔升格 |
| Fake verified／live | **OPEN** | 本頁只綁「唔准 stamp」 |
| 圖表／指標設計頁 | Draft（ns-1） | Desk TV-style 圖表範圍；本頁唔取代 |
| 參考訊號設計頁 | Draft（ns-2） | Desk 參考／advisory 訊號範圍；D7 schema 仍 planning；本頁唔取代 |
| AUDIT Confirmed remediation | **1–5 only** | **唔**加 item 6 |
| **Issue #10** | **OPEN** | Parent audit inventory remains OPEN |

---

## 9. Out of scope this page｜本頁／本週期唔做

- 改 runtime／Harness／strategy／rules；實作 Intent→M8、D7-DESIGN、Desk flatten。  
- 實作產品 M7（`backend/trading/advisory/`）或把 lab 接上執行路徑。  
- Stamp verified／live 為已上線產品。  
- 擴大 2026-10-21 Confirmed remediation（**保持 1–5**；本頁**唔係** item 6）。  
- 改寫 ADR-003 正文。復活 **ADR-002**。  
- 保證盈利；期權執行帳；黃金 SKU；多 Agent 辯論主引擎。  
- Issue #10 stays OPEN（父審計庫存繼續開；本頁唔當庫存完成）。

---

## 10. 同其他文件關係｜Authority

衝突時：

1. **ADR-003 + Addendum A**（決策＋北星＋§4 驗收＋Proposal 治理）  
2. **MODULAR_ARCHITECTURE**（M7／M8／M9、Intent v0、雙時鐘）  
3. **AUDIT_REMEDIATION_PLAN**（Confirmed items 1–5；P0 journal 先）  
4. **BOUNDED_INTELLIGENCE_ACCEPTANCE**（AI 能力切片接受條件）  
5. **ACCOUNT_MANAGEMENT_DESIGN**（管戶產品範圍）  
6. **本頁**（lab≠M7 隔離硬閘；Owner 接受後）  
7. 舊 roadmap／runbook（frozen；**ADR-002 is VOID**）

相關 Draft（唔升格）：[`CHARTS_INDICATORS_DESIGN.md`](CHARTS_INDICATORS_DESIGN.md)（ns-1 圖表／指標）；[`REFERENCE_SIGNALS_DESIGN.md`](REFERENCE_SIGNALS_DESIGN.md)（ns-2 參考訊號；D7 schema 仍 planning）。Issue #10 stays OPEN.

本頁**不改變** live RiskEngine／Kelly／mandate／kill 設定或交易 runtime。

---

*Drafted 2026-10-23 for plan-16 lab≠product M7 binding. Owner／PM 審批前維持 Draft。Merge ≠ Accepted. Issue #10 stays OPEN.*
