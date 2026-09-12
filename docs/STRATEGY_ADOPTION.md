# ALPHA//DESK — 策略引用採納報告（Strategy Adoption）

**Last updated:** 2026-09-12  
**Audience:** 本機／Cloud Cursor Agent、產品負責人  
**Method:** 三輪獨立覆核（①代碼對照名單盤點 ②投委／交易員應引審核 ③「唔好引」壓力測試）  
**Related:** [`ARCHITECTURE_DECISION.md`](ARCHITECTURE_DECISION.md) · [`PROCUREMENT_GUIDE.md`](PROCUREMENT_GUIDE.md) · [`LOCAL_CURSOR_HANDOFF.md`](LOCAL_CURSOR_HANDOFF.md) · [`AUTO_TRADING_ROADMAP.md`](AUTO_TRADING_ROADMAP.md)

---

## 0. 一句定位（強制遵守）

我哋係 **regime-aware long-only**：

- **質素／成長複合選股** + **上升趨勢內均值回歸（Stable）** + **VCP／突破（Aggressive）**
- 執行層用 **半 Kelly + 硬風控閘**（倉位帽、相關、日虧、殺開關、mandate、bracket）
- **下一步**：訊號統一、真相對強度、ATR 風險單位、付費日 K、多月 shadow 封印  
- **唔係**：FF 多空因子書、CTA／TSMOM 期貨盤、做市／HFT、端到端 RL／深度學習下單機

任何新策略／模型若**不能**寫成「可審計規則 + 風控硬閘 + OOS／shadow 封印」，**禁止**進入主交易路徑。

---

## 1. 第一輪 — 代碼已引用邊啲（對照市面／學術名單）

| 名單類別 | 狀態 | 置信度 | 說明 |
|----------|------|--------|------|
| 均值回歸（RSI + 布林帶 + SMA200 上升過濾） | **已引用** | 高 | Stable 主策略＝swing MR-in-uptrend |
| 趨勢／突破（SMA／MACD／ADX／ATR 停／VCP） | **已引用** | 高 | Aggressive ≈ Minervini SEPA／VCP；非 Donchian/Turtle |
| 相對強度 vs SPY／板塊 | **部分** | 中 | 短窗差價有；**非** 12 月 Jegadeesh 截面動量；docstring 宣稱 12 週 RS Rank **未完整編碼** |
| TSMOM／Carhart WML | **缺席** | — | — |
| 基本面「因子味」（成長／PEG／ROE／槓桿；價值／股息分數） | **已引用（啟發式）** | 高 | multi-metric 複合分，**非** FF HML/SMB/RMW 回歸組合 |
| 體制（SPY SMA + VIX → 曝險／權重） | **已引用** | 高 | 規則體制；**非** HMM |
| 情緒（VADER 類新聞分 ± 細權重；LLM 軟混） | **已引用** | 高 | FinBERT **未做**；LLM **不可**繞過風控 |
| 事件（業績衝突降權／黑窗） | **部分** | 中 | 有防護；**無** PEAD 交易規則 |
| 風險鐵則（半 Kelly、25%/90%、相關、日虧、殺開關、mandate、β／ATR、bracket） | **已引用** | 高 | 產品最大優勢層 |
| 驗證（walk-forward、成本、filing lag、校準閘 Kelly、PIT 宇宙） | **已引用／加強中** | 高 | 仍非機構 CRSP／purged CV |
| 組合（Kelly／等權 + 板塊帽） | **已引用** | 高 | **無** risk parity／Black–Litterman |
| 執行（Alpaca shadow／paper、bracket） | **已引用** | 高 | live 需人類閘 |
| Stat-arb／做市／HFT／VRP／GARCH／XGBoost／LSTM／PPO | **缺席** | — | roadmap 提及 ≠ 已上線 |

**指紋：** 體制感知質素成長選股 + MR-in-uptrend + VCP 突破袖 + 硬風控殼。

---

## 2. 第二輪 — 頂尖交易員視角：應該引用

帳戶形態＝個人／小帳戶美股**偏多**系統。Edge 來自選股＋擇時＋紀律，唔係延遲或做市價差。

### 高優先（同現有 DNA 合拍 — 應做）

1. **研究推薦器 ↔ 自動交易策略「單一真相」** — 門檻／停損／過濾必須對齊。  
2. **真·相對強度** — 12／6／3 個月 vs SPY 與同業；補齊已宣稱未編碼嘅 RS。  
3. **ATR／每筆風險% 倉位** — 同等風險單位，優於再堆因子名。  
4. **付費日 K（Polygon 級）+ 多月 shadow 封印** — 數據可信度 > 新模型名。  
5. **Quality 顯式拆賬** — ROE／利潤率／槓桿從成長雜燴拆出，方便歸因。

### 中優先（有條件先可做）

6. 截面動量只做**過濾／排序**，唔好獨立高換手 book。  
7. FinBERT／更好情緒：**只准軟門／解釋**，永遠唔准繞風控。  
8. PEAD：只可研究旗標或極窄 OOS 實驗，**唔好**併入 Stable 主路徑。  
9. 可選：QuantConnect／LEAN 做外部嚴格回測宿主；UI 仍留本 desk。

---

## 3. 第三輪 — 壓力測試：唔應該引用

| 唔好引（而家） | 原因 |
|----------------|------|
| HFT／做市／延遲套利／訂單流毒性 | 要共置、專用 feed、庫存希臘；日頻 stack 假回測 |
| 抄 RenTech／JS／Citadel Sec「黑盒」路徑 | 無同等基礎設施＝自我安慰 |
| 全市場 stat-arb／配對協整多空 | 要借券、中性、高頻再平衡；同 long-only Kelly 殼衝突 |
| VRP／賣波／複雜期權自動交易 | 尾部同保證金結構唔同；期權層仍係研究 |
| XGBoost／LSTM／Transformer／PPO **直接出倉** | 樣本小、難審計、易過擬合；未有研究工廠前禁止主路徑 |
| 完整 FF／AQR 多空因子組合 | 要空頭與純度定義；個人多頭用啟發式質素更匹配 |
| Bridgewater 式全球風險平價／全天候 | 資產類別同期貨槓桿超出產品邊界 |
| 未封印就加全市場 CTA TSMOM 期貨 | 另一業務線，搶複雜度 |
| Composer／Holly 等外部掃描當**核心 alpha** | 可作靈感；不可取代可審計自有規則 |
| 共享／來路不明行情 Key | 證據鏈斷裂，封印作廢 |

**紅線（三輪一致）：** 不能審計 + 不能過硬風控 + 不能 OOS／shadow 封印 → **禁止上線**。

---

## 4. 採納優先序（給 Agent 執行用）

| 序 | 動作 | 應／唔應 |
|----|------|----------|
| P0 | 統一 research 與 worker 訊號／門檻 | **應** |
| P1 | 實作真 RS（中期 vs SPY／同業） | **應** |
| P2 | 每筆風險%／ATR 單位 sizing | **應** |
| P3 | Polygon 日 K + 多月 shadow seal | **應** |
| P4 | Quality 因子拆賬歸因 | **應** |
| P5 | FinBERT 軟情緒／窄 PEAD 旗標 | 可選 |
| P6 | 外部 LEAN 回測 | 可選 |
| X | HFT／做市／stat-arb 中性／賣波自動／端到端 DL·RL 下單／FF 多空／風險平價宏觀 | **唔應** |

---

## 5. 給本機 Cursor 嘅強制 prompt 片段

```text
Read docs/STRATEGY_ADOPTION.md before changing strategy code.
Product DNA: regime-aware long-only quality/growth + MR-in-uptrend (Stable)
+ VCP breakout (Aggressive) + half-Kelly hard risk shell.
DO: unify signals, real relative strength, ATR risk units, paid bars, shadow seal.
DO NOT: HFT/MM, market-neutral stat-arb, VRP selling, end-to-end DL/RL orders,
full FF long-short, risk-parity global macro, or third-party scanners as core alpha.
LLM/sentiment must never bypass RiskEngine, mandate, or kill switch.
No live trading without paper/shadow seal per PAPER_VALIDATION_RUNBOOK.md.
```

---

## 6. 變更紀錄

| 日期 | 變更 |
|------|------|
| 2026-09-12 | 初版：三輪覆核寫入；應引／唔應引定稿並推送 `main` |
