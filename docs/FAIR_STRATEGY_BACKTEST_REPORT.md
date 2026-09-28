# Fair Strategy Backtest Report

**Generated (UTC):** 2026-09-28T01:34:18Z  
**Window:** `2023-09-28` → `2026-09-26`  
**Strategy source:** `origin/lab/citation-try` @ `852b4dd`  
**Universe loaded:** 74 / 74 tickers  

## Fairness protocol

| Knob | Value |
|------|-------|
| stable | Connors-style Stage-2 reversion (legacy id stable) |
| aggressive | VCP breakout + 12-week RS vs SPY + MACD hist (legacy id aggressive) |
| hybrid | Adaptive regime router (legacy id hybrid → AdaptiveStrategy) |
| Capital | $100,000 |
| Max positions | 10 |
| Sizing | risk_pct @ 0.01 risk, cap 0.25 |
| Transaction cost | 10.0 bps |
| Slippage | 5.0 bps |
| Fill model | signal on close → next-bar open + slippage |
| Fundamentals | neutral pass-through (stable 70, hybrid 60, aggressive 55) |
| Shared price snapshot | True |

**Supersedes:** Earlier run on origin/main 6ff4501 used the pre-rewrite RSI/BB Stable and VCP Aggressive.

**Excluded:** `research_list` — Depends on live Scan book; not historically reproducible.

**Known biases:**

- Survivorship bias: current-universe constituents only (yfinance).
- No point-in-time fundamentals / filings lag.
- No walk-forward / purged CV in this head-to-head run.

## Results

| Strategy | Kind | Total return % | Ann. return % | Excess vs SPY % | Sharpe | Max DD % | Win rate % | Profit factor | Trades | Avg invested % |
|----------|------|----------------|---------------|-----------------|--------|----------|------------|---------------|--------|----------------|
| stable | strategy | -25.47 | -9.35 | -112.12 | -0.80 | 28.10 | 62.08 | 1.15 | 1440 | 35.71 |
| aggressive | strategy | 22.39 | 6.98 | -64.26 | 0.68 | 10.01 | 42.11 | 1.53 | 95 | 35.38 |
| hybrid | strategy | -4.38 | -1.48 | -91.03 | -0.02 | 26.54 | 42.33 | 1.17 | 1115 | 68.42 |
| SPY_buy_hold | buy_hold | 86.65 | 23.16 | — | 1.45 | 18.76 | — | — | 1 | — |
| equal_weight_universe | buy_hold | 191.88 | 42.99 | — | — | — | — | — | 74 | — |

**Ranking by total return (strategies only):** aggressive, hybrid, stable

## Exit reason mix (strategies)

- `stable`: take_profit_rsi2=889, stop_loss=275, take_profit=255, take_profit_sma21=14, end_of_backtest=4, time_stop=3
- `aggressive`: stop_loss=46, take_profit=31, trend_break_sma50=16, end_of_backtest=2
- `hybrid`: atr_trail=603, stop_loss=251, take_profit=145, take_profit_rsi2=63, trend_break_sma50=48, end_of_backtest=4, take_profit_sma21=1

## How to reproduce

```bash
PYTHONPATH=/workspace:/workspace/backend python3 scripts/run_fair_strategy_backtest.py
```

Artifacts: `backend/data/fair_strategy_backtest_report.json`, `docs/FAIR_STRATEGY_BACKTEST_REPORT.md`.

