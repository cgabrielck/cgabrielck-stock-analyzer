# GitHub Upgrade Plan - Historical Snapshot

> This file was recovered from an interrupted upgrade session and is retained
> as historical design context. Its implementation status is stale. Use
> `UPGRADE_RECOVERY_LOG.md` as the authoritative upgrade status and resume
> checkpoint.

---

## 1. 市场情绪与成交额分析 (Market Sentiment & Deal Amount)

### 状态
- `sentiment_analyzer.py` 模块已创建（核心逻辑完成）
- **尚未接入** pipeline (`recommender.py` 未调用)
- **尚未接入** UI (`app.py` 未显示)
- **尚未编写** 测试
- **尚未添加** i18n 键

### 架构设计

```
fetch_all_stocks() → compute_all_technical()
    → sentiment_analyzer.compute_sentiment(news, tech_data)  ★ 新增
    → LLM (可选)
    → risk_adjusted → select_recommendations
                       ↕
        情绪/成交额仅做信息展示 + 同分 tiebreaker
```

### 模块结构

**`backend/agents/sentiment_analyzer.py`**（已完成）

```python
def compute_sentiment(news_articles, technical_data) -> dict:
    # 返回:
    {
        "composite_score": 0-100,
        "composite_label": "positive/neutral/negative",
        "dimensions": {
            "news": {"score": 0-100, "label": "...", "positive_pct": N, "negative_pct": N},
            "momentum": {"score": 0-100, "label": "..."},
            "volatility": {"score": 0-100, "label": "..."}
        }
    }
```

| 维度 | 权重 | 数据来源 |
|------|------|----------|
| 新闻情绪 | 40% | `news[].sentiment` — 现有 `_analyze_news_sentiment()` 关键词方案 |
| 动量情绪 | 35% | `rsi_14` + `macd_histogram` + `trend_signal` |
| 波动率情绪 | 25% | `atr_14 / price` — 低波动→稳定，高波动→警惕 |

### 成交额增强（待修改 `technical_analyzer.py`）

在 `compute_technical_indicators()` 新增字段：

```python
dollar_volume_10d_avg = float((close * volume).tail(10).mean())
dollar_volume_50d_avg = float((close * volume).tail(50).mean())
dollar_volume_ratio = dollar_volume_10d_avg / dollar_volume_50d_avg if dollar_volume_50d_avg > 0 else None
```

量价配合评分（OBV 简化版）：

```python
up_days = close.diff() > 0
volume_above_avg = (volume > volume.rolling(20).mean()).astype(int)
obv_signal = (up_days.astype(int) * 2 - 1) * volume_above_avg
# 上涨放量=+1，下跌放量=-1，缩量=0
volume_quality_score = obv_signal.tail(20).mean() * 50 + 50  # 归一化到 0-100
```

### scoring 选项

**选项 A（推荐，保守）**：情绪/成交额仅作为信息展示，不影响 `total_score`。只在同分时作为次要排序条件（tiebreaker）。风险最低。

**选项 B（适度）**：极端情绪时 ±3% 上限修正：

```python
if sentiment["composite_score"] >= 80:
    rec["total_score"] = min(100, rec["total_score"] * 1.03)
elif sentiment["composite_score"] <= 20:
    rec["total_score"] = max(0, rec["total_score"] * 0.97)
```

**选项 C（激进）**：UI 滑块控制情绪/成交额影响权重（类似 LLM 权重滑块），默认 10%。

### UX/UI 设计（参考 LingTrade 卡片风格）

推荐卡片新增两行徽章：

```
┌──────────────────────┐
│  RANK #1             │
│  NVDA  英伟达         │
│  半导体              │
│  ─────────────       │
│  $125.40             │
│  评分: 85.3          │
│  📰 乐观 | 💰 $2.3B/日 │  ← 新增
└──────────────────────┘
```

情绪用颜色图标：🟢 乐观 / 🟡 中性 / 🔴 谨慎
成交额显示：格式 `$2.3B/日` + 放量/正常/缩量

### 需要改动的文件

| 文件 | 改动 |
|------|------|
| `backend/agents/sentiment_analyzer.py` | ✅ 已完成 |
| `backend/agents/technical_analyzer.py` | +8 行：成交额计算、量价配合评分 |
| `backend/agents/recommender.py` | +5 行：导入 sentiment_analyzer，推荐字典追加字段 |
| `backend/app.py` | ~25 行：推荐卡片徽章、排行表格新列 |
| `backend/i18n.py` | +8 个键 × 3 种语言 |
| `tests/test_sentiment_analyzer.py` | 新建 ~40 行 |

---

## 2. 新闻情绪深度优化 (News Sentiment — Plan 6)

### 当前状态
- Phase 1 ✅ 关键词方案（`data_fetcher.py` 的 `_analyze_news_sentiment()`）
- Phase 2 ❌ VADER
- Phase 3 ❌ FinBERT / LLM 摘要

### Phase 2: VADER（~1 小时）

在 `data_fetcher.py` 中将 `_analyze_news_sentiment()` 升级：

```python
from nltk.sentiment.vader import SentimentIntensityAnalyzer

_sia = None
def _get_sia():
    global _sia
    if _sia is None:
        try:
            _sia = SentimentIntensityAnalyzer()
        except LookupError:
            import nltk
            nltk.download('vader_lexicon')
            _sia = SentimentIntensityAnalyzer()
    return _sia

def _analyze_news_sentiment(title: str, summary: str) -> str:
    sia = _get_sia()
    text = title + " " + summary
    scores = sia.polarity_scores(text)
    compound = scores["compound"]
    if compound >= 0.05:
        return "positive"
    elif compound <= -0.05:
        return "negative"
    return "neutral"
```

**依赖添加**：`nltk` 到 `requirements.txt`

**缺点**：需要下载 NLTK 语料库；Streamlit Cloud 冷启动时可能增加延迟。

### Phase 3: FinBERT（~2 小时，可选）

```python
from transformers import pipeline

_finbert = None
def _get_finbert():
    global _finbert
    if _finbert is None:
        _finbert = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            truncation=True, max_length=512,
        )
    return _finbert
```

**依赖添加**：`transformers` + `torch`（~2GB，可能不适合 Streamlit Cloud）

**替代方案**：使用 `finbert-tone` 轻量版本或缓存模型到 HuggingFace Hub。

### Phase 3b: LLM 摘要（可选）

对新闻调用 LLM 做结构化分析（类似 `picks_news.py` 的 `_ai_event_analysis`）。已经通过 `picks_news.py` 的 AI 影响分析间接完成。

### 关键权衡

| 方案 | 准确率 | 延迟 | 依赖大小 | 适用场景 |
|------|--------|------|----------|----------|
| 关键词 Phase 1 | 中 | <1ms | 无 | 当前默认 |
| VADER Phase 2 | 高 | <5ms | 小 (nltk) | 推荐下一步 |
| FinBERT Phase 3 | 很高 | ~200ms | ~2GB | Streamlit Cloud 受限 |
| LLM 摘要 | 最高 | ~2s | 无 | 已通过 picks_news 覆盖 |

**推荐**：先升级到 VADER（Phase 2），Phase 3 留作长期。

---

## 3. Alert Worker 部署

### 当前状态
- `backend/alert_worker.py` ✅ 已完成
- `005_alert_monitoring.sql` ✅ 已完成（未在 Supabase 运行）
- 生产部署 ❌ 未完成

### 部署选项

**选项 A：Railway**（推荐）

```bash
# railway.toml
[build]
  builder = "nixpacks"
  buildCommand = "pip install -r requirements.txt"

[deploy]
  startCommand = "python backend/alert_worker.py"
  healthcheckPath = "/"
```

环境变量：
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `ALERT_INTERVAL_SECONDS`（默认 60）

**选项 B：Render**

```yaml
# render.yaml
services:
  - type: worker
    name: stock-alert-worker
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: python backend/alert_worker.py
```

**选项 C：GitHub Actions Cron**

```yaml
on:
  schedule:
    - cron: '*/5 * * * *'  # 每 5 分钟
jobs:
  check-alerts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -r requirements.txt
      - run: python backend/alert_worker.py --once
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
```

**局限**：GitHub Actions 最小间隔 5 分钟，不如独立 worker 实时。

### 前提条件

1. 在 Supabase SQL Editor 按顺序运行：
   - `004_fix_saved_plan_rpc.sql`
   - `005_alert_monitoring.sql`
2. 创建 Railway/Render 项目
3. 配置环境变量
4. 启动 worker

---

## 4. 参考项目可借鉴的未来升级

### 4.1 TradingAgents-CN（30.7k ⭐）— 多智能体协作

**当前架构**：单线流程 `data → fundamental → technical → LLM → risk → select`
**可借鉴**：

- **分析师 → 交易员 → 风控** 的分工模式
- 每个 Agent 保留完整的推理链路
- 前端展示每一步的决策过程（如 LingTrade 的 Agent 决策页）

### 4.2 LingTrade（26 ⭐）— 界面设计

**可借鉴**：

- 7 模块 Dashboard：总览 / 行情 / 持仓 / Agent 决策 / 投研报告 / 知识库 / 设置
- Agent 决策的推理链透明展示
- 卡片式布局，信息层次分明
- 深色主题已经类似，但布局可以更清晰

### 4.3 ai-stock-report（11 ⭐）— 邮件推送

**可借鉴**：

- 每日定时自动分析，邮件推送 HTML 报告
- 评分不使用买卖标签，改用"谨慎/中性/乐观"
- `pending` 状态配置向导（`start.bat` 双击启动）

### 4.4 Moss（382 ⭐）— 五支柱策略系统

**可借鉴**：

- 五个独立支柱：Trend / Momentum / Mean-Reversion / Volume / Risk
- 各自输出归一化信号（0-100）
- 通过 LLM 反射每周自动演化参数
- Volume 支柱含 OBV 和量价相关性

### 4.5 潜在升级路线

| 优先级 | 功能 | 参考项目 | 工作量 |
|--------|------|----------|--------|
| P0 | 情绪+成交额展示 | 本次计划 | ~2h |
| P1 | VADER 新闻情绪升级 | ai-stock-report | ~1h |
| P2 | 邮件报告推送 | ai-stock-report | ~4h |
| P3 | Agent 推理链展示 | LingTrade, TradingAgents-CN | ~1d |
| P4 | 五支柱信号系统 | Moss | ~3d |
| P5 | 策略演化机制 | Moss | ~5d |

---

## 5. 数据库 Migration 状态

| 迁移文件 | 状态 | 备注 |
|----------|------|------|
| `001_accounts.sql` | ✅ 已在 Supabase 运行 | |
| `002_saved_plan_alerts.sql` | ✅ 已在 Supabase 运行 | |
| `003_saved_plan_outcomes.sql` | ✅ 已在 Supabase 运行 | |
| `004_fix_saved_plan_rpc.sql` | ❌ 未运行 | 修复 `save_saved_plan_version` 的 `ticker` 歧义 |
| `005_alert_monitoring.sql` | ❌ 未运行 | 需要先运行 004 |

**注意**：Git push 到 main 不改变 Supabase 数据库。必须手动在 Supabase SQL Editor 运行 SQL 内容。

---

## 6. 未处理的技术债务

- `.vscode/settings.json` — 本地修改，需保持 exclude
- `requirements.txt` 中 altair 已添加，但未确认 chart 使用了 altair
- 测试覆盖率 191/191，但缺少 sentiment_analyzer 测试
