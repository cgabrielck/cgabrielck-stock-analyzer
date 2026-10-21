# AUDIT_REMEDIATION_PLAN (2026-10-21)

審計補救計劃／Audit Remediation Plan — 交易應用（Trading Apps）

## Status

**Confirmed by Gabriel 2026-10-21 (Hong Kong time / Asia/Hong_Kong).**

Confirmed / binding next-doc work under ADR-003. Do not treat ADR-002 as binding.

狀態：已由 Gabriel 確認（2026-10-21，香港時間）。對後續文件工作具約束力；以 ADR-003 為準，**勿**再將 ADR-002 視為有效決策。

---

## Verdict

Keep ADR-003; revise docs before build. Risk if skip: Python rule kernel + docs claiming desk.

結論：保留 ADR-003，**先修訂文件再開工**。若跳過修訂風險：實際仍係 Python 規則内核，文件卻聲稱已是完整交易台（Desk）。

骨架對齊北極星，唔使推倒 ADR-003；但要先修訂再開工。

---

## Confirmed remediation (ordered)

已確認補救項（按優先序）

1. **Freeze/supersede old docs（凍結／取代舊文件）**  
   涵蓋：`PAPER_VALIDATION_RUNBOOK`（移除 ADR-002 precondition）、`AUTO_TRADING_ROADMAP`、`NEXT_PHASE_PLAN`、`QUALITY_ROADMAP`、`PROGRESS_LOG`。  
   頂欄橫幅：superseded by **ADR-003** + **MODULAR_ARCHITECTURE**；衝突一律以該兩份為準。

2. **README product section（README 產品段）**  
   Desk = product；Streamlit = lab/legacy。  
   產品面與 ADR-003「Desk＝產品」對齊，消除 research-terminal／Streamlit 敘事分裂。

3. **Intent v0 + 架構圖**  
   於 `MODULAR_ARCHITECTURE` §5 補 **Intent v0 schema**；更新 `boomboom-modular-architecture.html`：dual-clock、Proposal/Approve path、IntentStore、L1/L2。

4. **Lock P0 order decision（鎖定 P0 順序）** — **Confirmed 2026-10-21**  
   **First:** user-visible journal / provenance. **Later:** IntentStore (H1/H3 split).  
   已鎖定：先用戶可見 journal／provenance；IntentStore（H1/H3 拆分）其後。唔再當開放決策。唔好只強化後台 worker。

5. **One-pager bounded intelligence acceptance（有界智能驗收一頁）**  
   - closed veto codes v0  
   - counterfactual vs pure rules  
   - account-level AI scope vs single-name strategy（管戶 vs 單票）  
   - `lab/advisory_debate` ≠ 產品 M7；**禁止**將 lab debate 接到 OrderManager／落單路徑。

---

## Explicit non-goals this cycle

本週期明確不做

- No silent L2 constitution changes（唔准靜默改 L2：mandate／kill／風險上限／策略預設）
- No resurrecting ADR-002（唔復活 ADR-002）
- No claiming live readiness without evidence gates（無證據閘唔宣稱可 live）

---

## Authority

權威層級（衝突時由上至下）

**ADR-003 + Addendum A** > **MODULAR_ARCHITECTURE** > **this plan** > older roadmaps.

- ADR-003 + Addendum A（含 Learn→Proposal→Approve→Merge；L1 可自動，L2 只經提案）
- MODULAR_ARCHITECTURE（M1–M13、雙時鐘、HarnessProposal）
- 本計劃（Confirmed 2026-10-21）
- 舊路線圖／runbook（僅歷史或須頂欄標 superseded）

---

## Context from audit (reference)

審計背景（供對照，非另起決策）

**站得住：** ADR-003＋Addendum A；模組圖 M1–M13、M8 受保護；`backend/trading` 風險／kill／mandate／worker／deploy 可演進。

**關鍵缺口：** 舊文件仍綁 VOID ADR-002；視覺圖落後；IntentStore 有字無合約；README 產品分裂；管戶智能未模組化；代碼仍係單一 TradingWorker 約 60s 一轉，無 IntentStore／Proposal 實作。

**代碼 vs 文件：** 文件寫雙時鐘＋IntentStore；實際 cadence 偏日内／週一再平衡。前端 `web/` 仍薄；VPS 偏 worker-only。

---

*File purpose: freeze Gabriel-confirmed audit remediation as binding next-doc work. Do not expand scope beyond Confirmed remediation without a new approval.*
