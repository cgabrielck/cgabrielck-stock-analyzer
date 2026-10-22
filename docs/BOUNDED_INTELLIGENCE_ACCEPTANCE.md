# 有界智能接受頁｜Bounded Intelligence Acceptance

> **Status:** Draft（plan-14／AUDIT remediation item 5）— 待 Owner／PM 審；**未 Accepted、未 merge 前唔作產品宣稱依據。**  
> **Binds under:** [ADR-003 + Addendum A](ADR-003-live-ai-trading-desk.md)（Accepted）> [MODULAR_ARCHITECTURE.md](MODULAR_ARCHITECTURE.md) > [AUDIT_REMEDIATION_PLAN_2026-10-21.md](AUDIT_REMEDIATION_PLAN_2026-10-21.md)。  
> **Index:** [ARCHITECTURE_DECISION.md](ARCHITECTURE_DECISION.md)。**ADR-002 is VOID.**  
> **Does not elevate:** PATH、D7（參考訊號仍 planning）、O5、flatten — 本頁唔升格呢啲決策。  
> **Date:** 2026-10-23（Asia/Hong_Kong）

---

## 1. 目的｜Purpose

用**一頁**寫清產品對「有界智能」（bounded intelligence）嘅**接受條件**同**不得宣稱事項**。  
對齊北極星：遠端 Desk（圖表／指標／可追溯日誌／參考訊號 planning）；AI 幫管戶；硬風控之上先判斷；**真倉要過證據閘**；Code Review 擋假 source／假 summary／hardcoded 結論。

本頁**唔係**保證盈利、唔係「可上真倉」證書、唔係 SignalCard／策略 schema 定稿。

---

## 2. 有界智能係咩／唔係咩｜Definition

| 是 | 不是 |
|----|------|
| 規則判定 setup **VALID**（或參考訊號卡）之後，套用**閉合 veto**＋可選 **conviction tier** | 開放式「AI 覺得唔好就否決」 |
| Conviction **只可選** M8 sizer **已允許**嘅檔位；最終 qty **只由 M8** 出 | AI 傳 raw size／qty／notional 當最終倉 |
| News／headlines：**只否決，唔觸發**買入 | Headline 單獨觸發開倉 |
| **管戶層**輔助（帳戶級 veto／tier／解讀／digest→Proposal） | 單票「神秘策略」替代 M6 playbooks 或繞 M8 |
| `backend/trading/advisory/`（產品 **M7**） | `backend/lab/advisory_debate.py` 當產品 M7，或接到 `OrderManager`／落單 |
| H3 寫 Intent；H1 消費 Intent（無同步等 LLM） | LLM／Telegram／聊天渠道直接下單 |
| L1 可 runtime 自動；L2 只經 Proposal→Approve→Merge | 靜默改 mandate／kill／風險％／live 旗標 |

---

## 3. 接受條件｜Acceptance gates（必須同時滿足先「接受」該 AI 能力切片）

對齊 ADR-003 §4（A–D）＋ Addendum A.4；勾選前必須有**可核實證據**（測試／shadow／paper／audit／journal），唔接受口號。

### 3.1 A — 智能／賺錢對照（晉升 AI 層）

- [ ] Shadow／paper 上，**有 AI vs 純規則**同 universe 嘅 **R-expectancy** 對照。  
- [ ] E ≤ 0 **或**唔優於純規則對照 → **唔晉升** AI 層（唔入 live 產品路徑）。  
- [ ] 對照結果可追溯（日誌／報表路徑寫明）。

### 3.2 B — Veto 安全

- [ ] **僅**閉合 veto 原因碼（見 §4 v0）；未知 code ⇒ **唔准攔**（只 log）。  
- [ ] 每筆否決有 audit ＋ **counterfactual「若無 veto」**；有 shadow follow-up／校準或降級路徑。  
- [ ] 開放式判斷 veto = **拒收**（M13／Code Review）。

### 3.3 C — 真倉證據閘（無證據唔宣稱可 live）

- [ ] 晉升清單寫死：**filled-only**、**seal**、風控演練、**人簽**、**小倉先行**。  
- [ ] **明文：未成交 limit ≠ 證據。**  
- [ ] 未過閘 ⇒ 產品文案／銷售／Desk UI **唔准**宣稱「可 live／已就緒真倉」。

### 3.4 D — 工程邊界

- [ ] 新 PR 默認演進 `backend/trading/`；greenfield 重寫要 Owner 另決。  
- [ ] Code Review（M13）擋：**hardcoded 結論**、**假 source**、**假 summary**（餵其他 worker／Proposal／UI）。  
- [ ] Proposal 無 sources／假 summary ⇒ 拒收；未 Approve 時 L2 語意不變。  
- [ ] H1 路徑**無**同步 LLM 呼叫（架構檢查或測試）。

### 3.5 產品面（北星最小）

- [ ] 遠端可睇圖＋指標；交易日誌**用戶可見、可追溯**。  
- [ ] 參考訊號若露出：標 **planning**，有可見／可解釋／可溯源最小形狀（**唔**當 D7 已定稿）。

---

## 4. Closed veto codes v0｜閉合否決碼（產品 M7）

> 來源：`MODULAR_ARCHITECTURE` §5.3 `VetoRecord` 示例。擴碼 = 新 PR＋本表更新；**禁止**開放字串當否決。

| Code | 含義（摘要） |
|------|----------------|
| `CALENDAR_BLACKOUT` | 日曆／時段禁開倉 |
| `STALE_DATA` | 行情／研究數據過期 |
| `CORR_CAP` | 相關性／集中度上限 |
| `KILL_ACTIVE` | kill 已啟用 |
| `EVENT_FREEZE` | 事件凍結窗口 |

**規則：** `open_ended=true` 非法；唔喺表內嘅 code 唔准單獨構成否決。

---

## 5. 管戶 vs 單票｜Account-level vs single-name

| | 管戶（接受範圍） | 單票策略（唔由本頁升格） |
|--|------------------|---------------------------|
| 角色 | M7：veto／tier／解讀／digest | M6 playbooks：VALID／skip |
| 輸出 | Intent（side／stop／tier／expiry）或 VetoRecord | 策略評估訊號 |
| 禁 | 替代 M8；傳 raw size；lab debate 落單 | 把「AI 單票故事」當已過風控訂單 |

**接受陳述：** 產品接受嘅係**帳戶級有界輔助**，唔係「每隻股票一個不可審計黑箱策略」。

---

## 6. 不得宣稱｜Must not claim

未過對應閘之前，產品／文件／銷售／UI **禁止**宣稱或暗示：

1. **可上真倉／live ready**（無 filled-only＋seal＋人簽＋小倉等證據閘）。  
2. **保證盈利**或「永遠可晉升真倉」。  
3. **參考訊號／策略 schema 已定稿**（D7／SignalCard 仍 planning）。  
4. **Lab advisory debate = 產品 M7**，或已接到 OrderManager。  
5. **AI 可繞風控**、可自訂 raw 倉位、可靜默改 L2 憲法。  
6. **未成交 limit／紙上計劃 = 實盤證據**。  
7. **PATH／O5／flatten 已升格**（本頁明確唔升格）。  
8. **假 source／hardcoded／無 provenance** 嘅結論當已核實研究或可執行意圖。

---

## 7. 證據閘一覽｜Evidence gates (live)

| 閘 | 最低證據 | 失敗後果 |
|----|----------|----------|
| Filled-only | 只計成交，唔計掛住未成交 | 唔計入晉升證據 |
| Seal | Stage-2／封印產物通過 | 唔開 live 路徑 |
| Risk drill | 風控演練已觸發可證 | 唔晉升 |
| Human sign-off | Owner 明文批准 | 唔開 `--allow-live`／live 旗標 |
| Small capital first | 小倉先行紀錄 | 唔擴倉 |
| AI layer E | 有 AI 優於純規則（§3.1） | AI 層唔晉升 |
| M13 clean | 無假 source／hardcode／繞 M8 | PR／Proposal 拒收 |

---

## 8. Lab ≠ Product（硬隔離）

- `backend/lab/advisory_debate.py`：**≠** 產品 M7。  
- **禁止**將 lab debate 接到 `OrderManager`、broker、或任何落單路徑。  
- Lab 結論唔可餵主路徑當已核實 source（見 [CITATION_REFUSALS.md](CITATION_REFUSALS.md) 精神）。

---

## 9. 同其他文件關係｜Authority

衝突時：

1. **ADR-003 + Addendum A**（決策＋北星＋§4 驗收）  
2. **MODULAR_ARCHITECTURE**（M7／M8／Intent／VetoRecord／Proposal）  
3. **本頁**（AUDIT item 5 一頁化；執行檢查清單）  
4. 舊 roadmap／runbook（frozen；唔復活 ADR-002）

本頁**不改變** live RiskEngine／Kelly／mandate／kill 設定或交易 runtime。

---

## 10. Out of scope this page｜本頁範圍外

- PATH、D7 定稿、O5、flatten 升格。  
- IntentStore／H1–H3 實作細節（見 MODULAR §4.4／§5.6）。  
- 黃金／商品完整實作；多 Agent 辯論主引擎；LLM 直落單。

---

*Drafted 2026-10-23 for AUDIT remediation item 5 / plan-14. Owner／PM 審批前維持 Draft。*
