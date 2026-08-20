# ALPHA//DESK — 下一阶段详细计划 (Next Phase Plan)

Last updated: 2026-08-15
Author: agent session (build mode)
Status: **提案 — 待用户确认**

---

## 0. 外部来源调研结果（诚实记录）

用户指定三个灵感来源。以下是我实际能访问到的程度：

| 来源 | 访问状态 | 可数字化的启发 |
|---|---|---|
| **HKUDS/Vibe-Trading** (GitHub, 30.9k★) | ✅ 完整读取 README + News | 见 §0.1 |
| **World Monitor** (GitHub) | ⚠️ 部分 — 搜索未命中同名 quant repo；相关项目为实时市场/新闻监控看板（AGPL） | 见 §0.2 |
| **Threads @0nepercent_trader** 指定帖 | ❌ **无法读取** — 页面为 JavaScript 渲染，抓取返回空正文 | 见 §0.3（需用户补充） |

### 0.1 从 Vibe-Trading (HKUDS) 消化的可落地想法

Vibe-Trading 是一个"个人交易 Agent"框架，其工程实践与我们目标高度契合。可直接借鉴：

1. **Live 订单守卫三件套（最有价值）**：
   - `PreTradeAdvisoryInterface` — 一个**不可绕过**的下单前咨询审查层，记录审查但**不能跳过** mandate gate（授权门）、kill switch（急停开关）、audit trail（审计追踪）。
   - **Mandate gate**：交易必须在预先声明的"授权范围"内（标的白名单、最大金额、方向）。
   - **Kill switch**：一键停止所有新开仓。
2. **Read-only 优先的券商连接器**：新券商先接只读（账户/持仓/订单/历史），`place_order`/`cancel_order` 在"结构性 paper/live 边界"建立前**硬拒绝**。这正是我们 `APCA_PAPER` guard 的强化方向。
3. **IM 频道运行时**：把 agent 会话/研究结果推送到 Telegram/Slack/Discord 等。我们已有 Telegram 告警，可扩展到**交易运维通知**（成交、风控熔断、每日 P&L）。
4. **文件工具沙箱**：隔离读写根目录 + 沙箱测试。对应我们 systemd 的 `ReadWritePaths` 加固。

### 0.2 从 World Monitor 消化的想法

同名 quant 交易 repo 未搜到；最接近的是"实时市场/新闻监控看板"类项目。可借鉴的是**监控 UI 理念**：一个实时刷新的运维面板（持仓、regime、心跳、P&L、风险指标），而非量化逻辑本身。这与 §Phase C 的监控看板一致。

### 0.3 Threads @0nepercent_trader — 待补充

我无法读取该帖正文（JS 渲染）。**请用户提供以下任一**，我再据此补充计划：
- 帖子文字截图或复制文本；
- 帖子要点的中文/英文摘要。

在拿到内容前，我用可验证的 AI-trader 通行做法替代（交易日志复盘、盘前准备清单、conviction-based sizing、risk-first 纪律），并在 §Phase B/E 标注了"待 0nepercent 内容确认"的插入点。

---

## 1. 产品目标与当前定位回顾

**北极星目标**：把 ALPHA//DESK 从"风险感知型选股研究终端"演进为**可靠的自动交易系统**，路线：
研究验证 ✅ → 纸面交易 ✅ → **影子交易 🔄（当前）** → 小资金人工批准实盘 → 受控自动化。

**核心原则（来自 AUTO_TRADING_ROADMAP）**：不因回测好看就上实盘；每一阶段都要有**证据、操作控制、恢复流程**。

### 当前已就绪（本会话 + 历史）
- 策略引擎 Stable/Aggressive（3R）、市场 regime 曝险门控、次日开盘成交回测、时间止损。
- RiskEngine 2.0（集中度/曝险/相关性/日亏损熔断/VIX 衰减）、Kelly sizing、OrderManager 幂等、Reconciliation。
- 独立 Worker CLI（live 守卫 + heartbeat）、systemd 单元 + 部署 runbook。
- 342 测试通过。已有 `risk_analyzer.py`（VaR/Sharpe/Sortino/Beta）、`sentiment_analyzer.py`（VADER + 动量/波动）、`portfolio_manager.py`（journal/kelly/correlation）。

### 当前**最大阻塞**（决定下一步）
路线图 Stage 2 明确列出两个未完成项：
1. **策略信号尚未连接到 shadow 模式的 SignalProcessor**（自动信号 → 影子成交的闭环缺失）。
2. **缺少多月评估窗口**（30 天 paper、Sharpe>1.0 的实盘前门槛无法度量）。

**结论**：下一阶段主线 = 打通"信号→影子/纸面执行"闭环 + 建立**可度量的验证工厂**。安全加固与监控看板围绕这条主线展开。

---

## 2. 阶段化计划总览

| 阶段 | 主题 | 目标产出 | 预估工作量 | 依赖 |
|---|---|---|---|---|
| **A** | 影子/纸面验证闭环 | 信号→SignalProcessor(shadow)→绩效记录 + 多月评估报告 | 3–4 天 | 无 |
| **B** | 安全加固（借鉴 Vibe-Trading） | Kill switch + Mandate gate + 审计日志 + Telegram 运维通知 | 3–4 天 | A |
| **C** | 实时监控看板（借鉴 World Monitor） | Streamlit 运维页：心跳/持仓/regime/P&L/风险徽章 | 2–3 天 | A |
| **D** | 新闻情绪升级（Plan 6） | FinBERT 可选相位 + 情绪进入信号门控 | 1–2 天 | 无（可并行） |
| **E** | 实盘就绪门 (Stage 3) | 30 天 paper 验证 runbook + 人工批准流程 | 依赖时间 | A,B,C |

下面逐阶段给出**任务、文件、验收标准、风险**。

---

## Phase A — 影子/纸面验证闭环（主线，最高优先级）

### 动机
关闭路线图 Stage 2 的两个未完成项。没有这一步，"自动交易就绪"无法被**证据**支撑。

### A1. 信号 → SignalProcessor(shadow) 闭环
- **现状**：`Worker._run_strategy_signals` 入场直接走 `RiskEngine.evaluate_order` → `OrderManager.submit_new_order`（真实/纸面）。影子路径（`ShadowTradingEngine`）只在 UI 手动注入时用。
- **改动**：给 Worker 增加 `execution_mode: Literal["paper","shadow"]`。
  - `shadow` 模式下，风控批准后的订单交给 `ShadowTradingEngine.simulate_submission(order, next_bars=...)`，用**次日 bar** 模拟成交，写入独立的 `shadow_orders.json`，**不触达券商**。
  - `paper` 模式维持现状（Alpaca paper）。
- **文件**：
  - `backend/trading/engine/worker.py`（加 `execution_mode` 分支）
  - `backend/trading/engine/worker.py::main()`（加 `--mode shadow|paper`，默认 `shadow`）
  - `backend/trading/storage.py`（可选：`JSONOrderStore(path=...)` 支持自定义账本路径）
- **验收**：`--mode shadow --once` 跑一轮，产生 shadow 成交记录，`submit_new_order` 到券商的调用为 0；新增单测覆盖两种模式分流。

### A2. 绩效记录器（Performance Tracker）
- **新模块**：`backend/trading/performance/tracker.py`
  - 输入：影子/纸面成交流水（每笔 entry/exit、P&L、reason、strategy、regime）。
  - 输出：滚动指标——累计收益、胜率、Profit Factor、**Sharpe/Sortino（复用 `risk_analyzer`）**、Max DD、日收益序列。
  - 持久化：`data/performance_shadow.json` / `data/performance_paper.json`。
- **验收**：给定合成成交流水，指标计算与 `risk_analyzer` 一致；新增单测。

### A3. 多月评估报告脚本
- **新脚本**：`scripts/run_evaluation.py`
  - 用 `strategy_backtest` 在滚动窗口（如 6 个月，月度前移）跑 Stable/Aggressive/Hybrid，输出对比表 + 写入 `UPGRADE_LOG.md`。
  - 复用已存在的 `backtesting/statistics.py`（bootstrap CI）给出**置信区间**，避免"回测好看=能上"的陷阱。
- **验收**：脚本一键产出 Markdown 报告；离线（mock fetch）单测跑通骨架。

### A 阶段风险
- yfinance 幸存者偏差 & 中性基本面分数（已记录的已知局限）——报告需显式标注，不得据此宣称实盘表现。

---

## Phase B — 安全加固（借鉴 Vibe-Trading，实盘前必须）

### B1. Kill Switch（急停开关）
- **新文件**：`backend/trading/safety/kill_switch.py` — 基于文件的开关 `data/KILL_SWITCH`（存在即停）。
  - Worker 每 tick 先检查；命中则跳过所有**新开仓**（退出仍允许），写 heartbeat 标记 `killed=true`。
  - CLI：`python -m backend.trading.engine.worker --halt` / `--resume`。
- **验收**：单测——开关存在时 `submit_new_order` 的 BUY 调用为 0，SELL 不受限。

### B2. Mandate Gate（授权门）
- **新文件**：`backend/trading/safety/mandate.py` + `config/mandate.example.json`
  - 声明式授权：`allowed_symbols`、`max_notional_per_order`、`max_daily_orders`、`allowed_sides`、`max_total_exposure`。
  - 作为 `RiskEngine` 之前的**硬门**；越界一律拒绝并写审计。
- **验收**：越界订单被拒 + 审计留痕；单测覆盖每条约束。

### B3. 不可绕过的审计日志（Audit Trail）
- **新文件**：`backend/trading/safety/audit.py` — append-only JSONL（`data/audit/YYYY-MM-DD.jsonl`），记录每个决策点（signal、risk decision、mandate、fill、halt）。
- **验收**：一轮 shadow 跑完，审计文件按序完整；不可被普通流程覆盖删除（只追加）。

### B4. Telegram 运维通知（复用现有 `telegram_notifier`）
- 事件：成交、风控熔断/日亏损halt、kill switch 触发、每日 P&L 摘要。
- **文件**：`backend/trading/engine/worker.py`（在关键点调用）+ `backend/telegram_notifier.py`（新增交易事件格式化）。
- **安全**：token/chat_id 仅从环境变量读取，绝不入库入库。
- **验收**：mock notifier 断言事件被触发；无真实密钥出现在代码/测试。

### B 阶段（待 0nepercent 内容确认的插入点）
- 若该帖包含具体的**风险纪律规则**（如单日最大回撤、连亏停手、conviction 分级仓位），将并入 B2 mandate 与 sizing 参数。

---

## Phase C — 实时监控看板（借鉴 World Monitor）

### C1. Streamlit 运维监控页
- **新文件**：`backend/trading/ui/monitor.py` + 在主导航挂载。
- **内容**：Worker 心跳（读 `heartbeat.json`）、当前 regime + 曝险 vs target、持仓表 + 逐仓 P&L、组合级 P&L 曲线、**每标的风险徽章**（复用 `risk_analyzer.risk_label`）、审计事件流尾部。
- **只读**：面板仅读取持久化状态，不触发交易（符合路线图"UI 只读系统状态"）。
- **i18n**：3 语言键（zh_cn/zh_tw/en，遵循 `i18n.py` 规范）。
- **验收**：给定合成 heartbeat/journal，页面渲染无异常；i18n 键在 3 语言齐全（补 `tests/test_account_i18n.py` 同类校验）。

### C2. 风险徽章进入推荐流
- Plan 4 收尾：在推荐/持仓处显示 VaR95 / Sharpe / Beta 徽章（数据来自 `risk_analyzer`）。
- **验收**：单测校验徽章分级阈值。

---

## Phase D — 新闻情绪升级（Plan 6，可与 A 并行）

- **现状**：`sentiment_analyzer` 已有 VADER + 动量/波动；`llm_agent.analyze_news_impact` 已有 LLM 情绪。
- **Phase 1（已具备）**：VADER 快速情绪。
- **Phase 2（新增，可选）**：FinBERT 金融领域情绪（`transformers` + 轻量模型），带**优雅降级**（缺库/离线回退 VADER），避免拖慢 Worker。
- **接入点**：情绪作为**软门**微调 `confidence`，不改变确定性风控（符合"LLM/情绪只解释，不绕过风控"）。
- **验收**：FinBERT 不可用时自动回退且不报错；单测覆盖回退路径。

> 依赖体积提示：FinBERT 会引入 `torch`/`transformers`（数百 MB），与 Hetzner CPX21（4GB）需评估。建议设为**可选 extra**，默认关闭。

---

## Phase E — 实盘就绪门 (Stage 3, 时间性)

- **前置**：A/B/C 完成。
- **产出**：`docs/PAPER_VALIDATION_RUNBOOK.md` — 30 天 paper 交易验证流程：每日记录、Sharpe>1.0 门槛、最大回撤红线、连亏停手、人工批准 checklist。
- **人工批准实盘**：`--allow-live` + mandate + kill switch 三重保护下，小资金、每单人工确认。
- **注意**：这是**时间性验证**，无法用代码跳过；本阶段只交付流程与工具，不代表可立即实盘。

---

## 3. 建议执行顺序与里程碑

```
里程碑 M1（A 完成）：影子闭环 + 绩效记录 + 评估报告  →  有"证据工厂"
里程碑 M2（B 完成）：kill switch + mandate + 审计 + Telegram  →  实盘前安全底座
里程碑 M3（C 完成）：监控看板 + 风险徽章  →  可观测性
里程碑 M4（D 可选）：FinBERT 情绪  →  信号质量
里程碑 M5（E）：30 天 paper 验证  →  实盘就绪判定
```

**建议先做 A（主线）**：它直接关闭路线图当前阻塞，且为 B/C/E 提供数据基础。

---

## 4. 全局约束与非目标

- **确定性风控不可被 LLM/情绪绕过**（贯穿始终）。
- **默认 paper/shadow**；实盘需显式多重开关。
- **VPS 成本**：默认功能须能在 Hetzner CPX21（4GB）运行；重依赖（FinBERT/torch）设为可选。
- **秘钥安全**：所有 token/key 仅环境变量；`.env` 不入库。
- **非目标**：期权自动交易、做市、高频；本阶段不涉及。

---

## 5. 待用户确认事项

1. **执行顺序**：确认"先 A 主线"，还是优先其他阶段？
2. **Threads @0nepercent_trader 内容**：请提供截图/文本，以便并入 B/E 的风险纪律。
3. **FinBERT（Phase D）**：是否接受可选重依赖？还是保持 VADER 即可？
4. **Telegram 运维通知**：复用现有告警 bot，还是单独 bot？
