# Fair Strategy Backtest Report

**Generated (UTC):** 2026-09-28T01:15:49Z  
**Window:** `2023-09-28` → `2026-09-26`  
**Universe loaded:** 74 / 74 tickers  

## Fairness protocol

| Knob | Value |
|------|-------|
| Capital | $100,000 |
| Max positions | 10 |
| Position slice | 5.0% of cash |
| Transaction cost | 10.0 bps |
| Slippage | 5.0 bps |
| Fill model | signal on close → next-bar open + slippage |
| Fundamentals | neutral pass-through (70 stable/hybrid, 50 aggressive) |
| Shared price snapshot | True |

**Excluded:** `research_list` — Depends on live Scan book; not historically reproducible.

**Known biases:**

- Survivorship bias: current-universe constituents only (yfinance).
- No point-in-time fundamentals / filings lag.
- No walk-forward / purged CV in this head-to-head run.

## Results

| Strategy | Kind | Total return % | Ann. return % | Excess vs SPY % | Sharpe | Max DD % | Win rate % | Profit factor | Trades |
|----------|------|----------------|---------------|-----------------|--------|----------|------------|---------------|--------|
| stable | strategy | 7.51 | 2.45 | -79.14 | 0.88 | 3.83 | 57.39 | 1.60 | 176 |
| aggressive | strategy | 11.12 | 3.58 | -75.53 | 1.02 | 3.67 | 44.72 | 1.70 | 123 |
| hybrid | strategy | 3.97 | 1.31 | -82.68 | 0.53 | 3.12 | 50.53 | 1.51 | 566 |
| SPY_buy_hold | buy_hold | 86.65 | 23.16 | — | 1.45 | 18.76 | — | — | 1 |
| equal_weight_universe | buy_hold | 191.88 | 42.99 | — | — | — | — | — | 74 |

**Ranking by total return (strategies only):** aggressive, stable, hybrid

## Interpretation (fair-test caveats)

1. **Cash drag is large.** Each entry uses a fixed **5% of cash** slice, capped at **10 positions** → theoretical max equity exposure ≈ **50%**. SPY / equal-weight benchmarks are **100% invested**. Absolute return gaps vs SPY are therefore not an apples-to-apples alpha claim without scaling exposure.
2. **Risk side favors the strategies.** Max drawdowns stayed ~3–4% vs SPY ~19% over the same bullish window — consistent with partial investment + tight stops.
3. **Aggressive ranked first** among the three (highest return, Sharpe, profit factor) with fewer trades; **Hybrid over-traded** (566 trades, highest fees) and ranked last.
4. **Stable hit its design win-rate band** (~57% vs target ~62%) but mean-reversion under-participated in a strong trend market.
5. **`research_list` was not backtested** — it needs a live Scan book and is not historically reproducible under this protocol.

## Exit reason mix (strategies)

- `stable`: take_profit_bb_mid=65, stop_loss=64, take_profit=23, time_stop=13, take_profit_rsi=6, end_of_backtest=5
- `aggressive`: trend_break_sma50=49, stop_loss=35, take_profit=19, overbought_rsi=18, end_of_backtest=2
- `hybrid`: trend_break_sma50=301, take_profit_rsi=221, stop_loss=24, take_profit_bb_mid=16, take_profit=4

## How to reproduce

```bash
PYTHONPATH=/workspace:/workspace/backend python3 scripts/run_fair_strategy_backtest.py
```

Artifacts: `backend/data/fair_strategy_backtest_report.json`, `docs/FAIR_STRATEGY_BACKTEST_REPORT.md`.

