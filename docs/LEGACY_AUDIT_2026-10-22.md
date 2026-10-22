# Legacy hot-path honesty audit（存量 fail-case 閘門）

| 欄位 | 值 |
|---|---|
| Date | 2026-10-22（Asia/Hong_Kong） |
| Repo | `cgabrielck/cgabrielck-stock-analyzer` |
| Baseline SHA | `6b61648268f1a671def201771ec50e5a6fdae7d5`（`main`，含 PR #8 journal） |
| Auditor | Code Review（fail-case-pr-gate 精神） |
| Scope | 舊有但仍在 Desk／worker 熱路徑嘅功能；**唔係**最近 PR 改碼 |
| Out of scope | ADR-002 VOID；lab `advisory_debate`（只附註）；PR #9 IntentStore（仍 draft，雙閘 APPROVE，等 Gabriel merge——**唔當 legacy 改碼**）；PR #8 journal（新 baseline，唔重寫） |
| Actions taken | **只報告**；未改碼、未開 remediation PR、未 merge |

## 原則

Fail case 必須令系統顯示失敗／拒絕／未驗證。若仍顯示正常／verified／live／filled／成功，該層視為有問題（dishonest success）。

## Authority（PM 鎖定）

1. Gabriel north star + evidence gates  
2. ADR-003 + MODULAR（binding）  
3. AUDIT remediation plan（P0 visibility-first）  
4. Self-upgrade：Harness 可提案，Gabriel 批准前唔准改 live config／strategy／rules  

掃序：Risk／mandate／kill → OrderManager／broker →（待）M8／StrategyBase → Desk／portfolio → lab 附註  

---

## Executive summary

兩刀存量 audit 均 **REQUEST_CHANGES**。

最大系統性缺口：**Desk 手動交易路徑同 worker bot 路徑閘門唔對稱**——worker 有 kill→mandate→size→RiskEngine→OrderManager；Desk `POST /api/orders` 多數只查 kill 後直呼 Alpaca。疊加 shadow／sync／performance 用假或替代成交價，會令「看起來成功」但證據不足。

| Slice | Verdict | HARD 數 |
|---|---|---|
| Risk／mandate／kill | REQUEST_CHANGES | R1–R5 |
| OrderManager／broker／shadow／performance | REQUEST_CHANGES | O1–O6 |

---

## Slice A — Risk／mandate／kill

**Pytest：** `test_kill_switch` + `test_mandate` + `test_gates` + `test_position_sizer` + `test_safety_audit` → **36 passed**（單元測綠，但未蓋 Desk 繞閘）。

| ID | 嚴重度 | 問題 | 證據 | Fail-case（應失敗但而家點） | 影響面 |
|---|---|---|---|---|---|
| R1 | **HARD** | Desk 手動單只查 kill，**唔經 MandateGate／RiskEngine**，直呼 broker | `backend/api/app.py:272-298` | Worker 擋 GME／超額／零組合；Desk Buy 仍可 submit | Desk 手動紙上交易；mandate／Risk 只綁 bot |
| R2 | **HARD** | 缺／壞 `mandate.json` → **fail-open permissive**；缺檔連 warning 都冇 | `mandate.py:109-118`；repo 無 `mandate.json`（gitignore），只有 example | Ops 以為有 Wave-1 收緊，實際任意 symbol／無限日單 | Worker `load_mandate()`；ops API 無 missing 旗 |
| R3 | **HARD** | `blocked_symbols`／allow-list 只喺 worker | `worker.py:533-554`；Desk 無對應 | Buy GME：worker BLOCKED；Desk 仍可落單 | 同 R1 |
| R4 | **HARD** | `max_daily_orders` 只係 **worker 行程內記憶體**；Desk 唔計、重啟歸零 | `mandate.py:128-147,193-199`；`worker.py:640` | 日限 5：worker 第 6 張擋；Desk 可繼續；restart 歸零 | 日額可被繞過 |
| R5 | **HARD** | RiskEngine（零組合／無 ref price／集中度／日虧…）**Desk 永遠唔打到** | `gates.py:71-92`；`signal_processor.py:87-112`；Desk `app.py:291` | portfolio≤0 或 market 無價：worker reject；Desk 仍交 Alpaca | 手動單只剩券商側拒絕 |
| R6 | 通過 | Kill engaged → Desk buy **409**；sell 放行；worker 擋新買 | `app.py:275-276`；`worker.py:231-243,524-531` | Kill ON + Desk buy → 409（代碼保證） | Desk↔worker 共用 `data/KILL_SWITCH` |
| R7 | 建議 | Kill sentinel 無檔鎖；TOCTOU；`.gitignore` 路徑錯 | `kill_switch.py:21-41`；gitignore 寫錯路徑 | 極端競態；sentinel 可能被 commit | 多進程 Desk + worker |
| R8 | 建議 | Worker mandate notional 用 qty=1 placeholder，Kelly 後唔再 evaluate | `worker.py:535-544` | `max_notional_per_order` 低於實際 Kelly 單仍可能 approve | Worker 授權 notional 弱 |
| R9 | 建議 | 無 API／integration 測 Desk kill→409 | 只有模組測 | 回歸可改壞 409 而單元測仍綠 | CI 缺口 |
| R10 | 建議 | Desk submit 唔寫 audit `log_event` | `app.py:291-295` vs worker audit | Journal 可能見到單但缺決策鏈 | 取證完整度 |

**Desk submit 精確路徑：** `_require_paper_operator` → `_require_paper()` →（僅 buy）`kill_switch.is_halted()`→409 → 組 Order → `_broker.submit_order` → 可選 `_store.save_order`。**無 mandate、無 RiskEngine、無 Kelly、無 SignalProcessor。**

### 防禦建議（A，未開 PR）

1. Desk `submit_order` 共用 `MandateGate.evaluate` +（可選）`RiskEngine.evaluate_order`  
2. `load_mandate`：缺檔亦 warning；`/api/ops` 加 `mandate_status: loaded|missing|permissive_fallback`  
3. 日額持久化並 Desk／worker 共用；Desk 成功買亦 `record_order`  
4. Worker mandate 用 Kelly 後真實 qty 再 evaluate  
5. 修正 `.gitignore`；加 API 測 halt→buy 409  
6. Desk submit 寫 audit（`actor=desk`）+ `record_from_order`

---

## Slice B — OrderManager／broker／shadow／performance

**Pytest：** order_manager + shadow + alpaca_broker + paper_performance 相關 → **28 passed**（部分測試**固化**咗不誠實成功，見 O8）。

| ID | 嚴重度 | 問題 | 證據 | Fail-case | 影響面 |
|---|---|---|---|---|---|
| O1 | **HARD** | Shadow 無 forward bars → 仍 `FILLED`；無 limit 時價＝**0.0** | `shadow.py:80-85` | `next_bars=None` + market → 成功成交 @ 0 | Shadow ledger／PnL／notify |
| O2 | **HARD** | `AlpacaBroker.get_order` **從不**映射 `filled_avg_price` | `alpaca_broker.py:221-244` | Broker 已 filled，本地永遠無成交價 | sync／notify／performance |
| O3 | **HARD** | `OrderManager.sync_order_status` 升格 `FILLED` **唔複製**成交價 | `order_manager.py:103-112` | broker=`9.87` → sync 後仍 `None` | 帳本 filled 無價 |
| O4 | **HARD** | `paper_performance`／`tracker` 用 **`limit_price` 當 fill** | `paper_performance.py:47,84`；`tracker.py:118,166` | filled + avg=None + limit=100 → turnover 當 100 | 誇大 turnover／digest |
| O5 | **HARD** | 雙執行路徑：Worker＝OM＋Risk；Desk＝直呼 Alpaca，常無 journal／audit | Desk `app.py:272-298` vs `signal_processor.py` | 人手單入 broker 但 journal／mandate 空白 | 可追溯性；疊加 R1 |
| O6 | **HARD** | Worker 填價 fallback：`filled_avg_price or entry_price`／`or limit_price or 0` | `worker.py:669,738` | sync 後無 avg → notify 顯示假價 | 操作者以為有真實 fill |
| O7 | 建議 | `OrderManager.submit_new_order` 自行標 `RISK_APPROVED`（「暫直接過」） | `order_manager.py:61-65` | 直呼 OM ＝紙上已風控 | 雙路徑 |
| O8 | 建議 | 測試固化不誠實：`test_no_forward_bars_falls_back_to_instant_fill` 期望 FILLED@limit | `test_shadow_v2.py:88-93` | Fail-case 被當 feature | 回歸唔擋 O1 |
| O9 | 通過 | Secrets 只讀 env；`APCA_PAPER` 預設 true；live 要 `--allow-live` | `alpaca_broker.py`；`worker.py:1196-1201` | — | — |
| O10 | 通過（對照） | PR#8 `trade_log`：**唔**用 limit 當 fill | `trade_log.py:198-227` | 無價 → 唔升格 journal filled | Journal 誠實；performance／shadow／sync **未對齊** |

### 防禦建議（B，未開 PR）

1. Shadow：無 bars／無有效價 → CANCELLED／REJECTED，禁止 `FILLED @ 0`；改 O8 測試為 fail-case  
2. Alpaca `get_order`／`submit_order`：映射 `filled_avg_price`  
3. `sync_order_status`：複製 fill 價；無有效價唔標 FILLED（對齊 journal）  
4. performance／tracker／worker notify：禁止 `limit_price` 頂 fill  
5. Desk POST：經 Mandate+Risk+OM，並 `record_from_order`（接 R1）  
6. 補 fail-case 測：無價 FILLED、sync 丟價、performance 唔用 limit  

---

## 熱路徑地圖（簡）

| 模組 | 熱／冷 | 備註 |
|---|---|---|
| kill_switch | Hot | Desk+worker 共用；buy-only halt |
| mandate | Hot | Worker 閘；Desk **唔用**；缺檔 fail-open |
| RiskEngine | Hot | 僅 SignalProcessor／worker |
| SignalProcessor | Hot | Worker M8→M9 |
| OrderManager | Hot | Worker／paper；Desk 多數繞過 |
| AlpacaBroker | Hot | Worker OM + **Desk 直呼** |
| shadow | Hot | execution_mode=shadow |
| paper_performance | Hot | Desk digest／performance |
| journal／provenance（PR#8） | Hot（新） | **唔當 legacy 債務** |
| IntentStore（PR#9） | 未入 main | 等 merge；唔當 legacy 改碼 |
| advisory_debate | Lab | 唔當 product blocker |
| Streamlit portfolio | Lab／舊 UI | 非 Desk L2 |

---

## 建議 remediation 排期（供 PM／Gabriel；今次未開工）

| 優先 | 項 | 對應 ID |
|---|---|---|
| P0 | Desk POST 接入 Mandate+Risk（+ audit／journal） | R1,R3,R4,R5,O5 |
| P0 | Fill 價誠實鏈：broker map → sync → 禁 limit 頂替 → shadow 無價唔 filled | O1–O4,O6 |
| P1 | Mandate 缺檔明示 + 非 fail-open（或至少 ops 旗） | R2 |
| P1 | 日額持久化共用 | R4 |
| P2 | Kill 檔鎖／gitignore；API 測 409；改 shadow 固化測 | R7,R9,O8 |

**軌道：** 存量 remediation **唔綁** PR #9 merge 決定。

---

## 未完成下一刀

- M8／StrategyBase  
- Desk 舊 portal／portfolio（中優先 UX／可追溯）  
- lab 附註掃  

---

## 變更紀錄

| 日期 | 內容 |
|---|---|
| 2026-10-22 | 初版：Inventory + Risk 切片 + OrderManager／broker 切片；上載 GitHub 留底 |
