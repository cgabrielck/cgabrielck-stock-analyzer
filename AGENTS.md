# Stock Analyzer — Enhancement Plan

## Architecture Decision (ADR-001)

**Keep the Streamlit research UI; upgrade execution and paid/PIT data — do not rebuild from scratch.**  
See [`docs/ARCHITECTURE_DECISION.md`](docs/ARCHITECTURE_DECISION.md) and [`docs/AUTO_TRADING_ROADMAP.md`](docs/AUTO_TRADING_ROADMAP.md).

**What to buy (VPS / broker / paid APIs):** [`docs/PROCUREMENT_GUIDE.md`](docs/PROCUREMENT_GUIDE.md) — Phase 1 Alpaca Paper + Hetzner CPX21 + Telegram; Phase 2 Polygon daily bars; Phase 3 live only after paper gate.

**Local Cursor handoff (plans / todos / reports pack):** [`docs/LOCAL_CURSOR_HANDOFF.md`](docs/LOCAL_CURSOR_HANDOFF.md) — paste into local Agent to continue this work.

## Quick Wins (1-2 hours total)

| # | Task | File | Status |
|---|------|------|--------|
| QW1 | Fix test_constants.py stock count 30→44 | `tests/test_constants.py:10` | ✅ |
| QW2 | Add altair to requirements.txt | `requirements.txt` | ✅ |
| QW3 | Cache thread safety (threading.Lock) | `backend/utils/cache.py` | ✅ |
| QW4 | LLM score as modifier (80/20 blend) | `backend/agents/llm_agent.py:164-165` | ✅ |
| QW5 | Fix valuation tab broken P/S P/B labels | `backend/app.py:1454-1455` + `backend/i18n.py` | ✅ |
| QW6 | Parallelize technical analysis | `backend/agents/technical_analyzer.py:200-206` | ✅ |
| QW7 | Extract duplicate `_get_latest_price()` | `backend/utils/price_utils.py` (new) | ✅ |
| QW8 | Rotate API key | `.env` (manual, user action) | 🔑 manual |

## Critical Enhancements (ordered by impact)

### Plan 1: Backtesting System (2-3 weeks)
- Validate scoring system's predictive power historically
- New module: `backend/backtesting/engine.py`
- Walk-forward validation across 5 years
- Monthly rebalancing simulation vs SPY

### Plan 2: LLM as Assistant ✅
- 80/20 quant/LLM score blend (already QW4)
- Sidebar slider for LLM influence %
- Score breakdown in UI — `fund_score` / `llm_score` / `total_score` side by side
- Divergence flag — warning when |fund_score - llm_score| > 25
- i18n keys in all 3 languages

### Plan 3: Portfolio Management ✅
- New module: `backend/agents/portfolio_manager.py`
- Kelly Criterion position sizing (capped at 25% per position, 90% total)
- Trade journal (JSON file) + automated buy logging
- P&L tracking on each position + portfolio level
- Correlation check (flags pairs with ρ ≥ 0.80)
- Stop-loss management (beta-based, default 10%, capped at 25%)
- Portfolio capital config in sidebar ($1K–$10M)
- Portfolio tab with position table, risk alerts, trade journal, reset button
- i18n keys in all 3 languages

### Plan 4: Risk Metrics (3 days)
- New module: `backend/agents/risk_analyzer.py`
- VaR 95%, Sharpe/Sortino, Max DD, Beta, Volatility
- Risk badge per recommendation
- Correlation matrix heatmap

### Plan 5: Market Regime in Core Pipeline (2 days)
- `detect_global_market_regime()` using SPY + VIX
- Adjust ENTRY_THRESHOLD and weights by regime
- Regime banner in UI

### Plan 6: News Sentiment Improvement (2 hours–1 day)
- Phase 1: VADER (quick)
- Phase 2: FinBERT (accurate)
- Phase 3: LLM summarization (optional)
