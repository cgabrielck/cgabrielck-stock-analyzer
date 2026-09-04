# TRADING_PHILOSOPHY — The Stock Analyzer Trading Brain

This is the durable knowledge base for how this project thinks about markets.
It is my persistent memory across sessions: I cannot change my own model weights
or learn live from the internet, so this file *is* the accumulated trading
mindset. Every session reads it; every meaningful trade decision updates it via
`TRADE_JOURNAL.md`.

**Honesty boundary.** Sections marked `[VERIFIED]` come from reliable public
sources (fetched 2026-08). Sections marked `[CLASSIC]` are well-established
public trading knowledge. Sections marked `[PRINCIPLE]` are general quant
frameworks. Nothing here is a promise of profit; markets are adversarial and
regime-dependent.

---

## 0. The prime directive: fewer mistakes beats more knowledge

J Law's central lesson `[VERIFIED]`: after reviewing thousands of his own
trades, he found losses came from a handful of *recurring errors* — not cutting
losses, mistiming entries, revenge sizing. His breakthrough was not learning
more; it was making fewer mistakes.

> "The real enemy isn't the market — it's myself. I have to be disciplined."

Implication for this project: the scoring system's job is not to be clever. It
is to be *consistent and disciplined* — to remove emotional, one-off errors from
the loop. A boring system that never blows up beats a brilliant one that
occasionally does.

---

## 1. Risk management is the foundation (non-negotiable)

`[VERIFIED]` J Law repeatedly stresses that the cornerstone of investing is
always knowing when to stop losses. His own near-ruin (a 3x-leveraged short-
Brazil ETF he refused to cut from -5% to -100%) is the cautionary tale:

- A small loss you refuse to take becomes a large loss you *cannot* take.
- Leverage + refusal to stop = account death.

**Rules encoded / to encode in this project:**
1. Every position must have a predefined stop before entry. The
   `portfolio_manager` uses a beta-based stop (default 10%, capped 25%).
2. Position size is capped: ≤25% per position, ≤90% total (Kelly, fractional).
3. Never average down into a losing thesis to "save" it. Averaging down is only
   valid as a *pre-planned scale-in* within a still-valid setup.
4. Correlation guard: flag pairs with ρ ≥ 0.80 — correlated positions are one
   position wearing two hats.

---

## 2. A system with positive expectancy beats stock-picking

`[VERIFIED]` J Law: success hinges not on any single stock but on a logical
trading system with positive expectancy. This is exactly why **Stage 4
(backtesting)** is the highest-value work: a score is worthless until its
expectancy is measured out-of-sample.

Expectancy = (Win% × AvgWin) − (Loss% × AvgLoss). A system can win 40% of the
time and be highly profitable if AvgWin >> AvgLoss. This reframes the goal:
**cut losers fast, let winners run** — not "be right often."

---

## 3. The classic masters (and what each contributes to our pillars)

Our engine scores five pillars: `trend, momentum, mean_reversion, volume, risk`
(`backend/agents/signal_pillars.py`). Each master maps to a pillar:

- **Jesse Livermore** `[CLASSIC]` — trade the trend, add to winners, cut losers
  instantly, respect the "line of least resistance." Feeds → `trend`, `momentum`.
  Cautionary: his own ruin came from breaking his own rules. Discipline > edge.
- **Richard Wyckoff** `[CLASSIC]` — price/volume tells the story of
  accumulation vs distribution; follow the "composite operator." Feeds →
  `volume`, `trend`. The volume pillar is Wyckoffian at heart.
- **Nicolas Darvas** `[CLASSIC]` — box theory: buy breakouts from consolidation
  boxes on rising volume, trail stops under each new box. Feeds → `momentum`,
  `mean_reversion` (box edges), `risk` (trailing stop).
- **Mark Minervini** `[CLASSIC]` — SEPA / Trend Template: only buy stocks in
  confirmed Stage-2 uptrends (price > 50 > 150 > 200 SMA, 200 rising),
  strong relative strength, tight risk. Feeds → `trend` gate + `risk`.
  Note: J Law broke Minervini's USIC record (353.9% vs 334.8%), and works in a
  compatible momentum/trend-template style.

**Current entry logic vs the masters:** our default enters on RSI-oversold +
lower-Bollinger + above-SMA200 — a *mean-reversion-in-an-uptrend* setup. That is
sound but is the opposite of pure Minervini breakout buying. The regime layer
(Stage 5) should decide *which* style is favored: mean-reversion in calm/
range regimes, breakout/trend-following in strong-trend regimes.

---

## 4. Multi-strategy frameworks `[PRINCIPLE]`

- **Trend following**: positive expectancy from fat right tails; low win rate,
  large winners. Needs strict stops and the discipline to sit through chop.
- **Mean reversion**: high win rate, small winners, occasional large loss if a
  reversion becomes a trend. Must be gated by a regime filter and a hard stop.
- **Momentum/relative strength**: buy strength, sell weakness; works until sharp
  reversals. Pair with volatility-aware sizing.
- **Risk parity / vol targeting**: size by inverse volatility so no single name
  dominates portfolio risk. Informs the beta-based stop and position caps.
- **Kelly criterion**: optimal geometric growth sizing; always use *fractional*
  Kelly (¼–½) because full Kelly is too volatile and assumes known edge. Already
  capped at 25%/position in `portfolio_manager`.

---

## 5. Orderflow / market-structure lens (supplement) `[VERIFIED, scope-limited]`

a2ky9 (HK intraday orderflow/ICT trader) works a *different timeframe* than this
project (futures/prop intraday, 1:5 RR, liquidity sweeps, FVG, order blocks,
BOS/CISD). Directly transplanting intraday orderflow into a swing stock scorer
is a category error. What we *can* borrow as a lens:

- **Liquidity awareness**: prior-day high/low and obvious swing highs/lows act
  as magnets and stop-hunt zones. Useful for placing stops *beyond* obvious
  liquidity, not on top of it.
- **Structure confirmation**: a break of structure on volume is more meaningful
  than a break on thin volume — reinforces the Wyckoff volume pillar.

This stays a supplement. The backbone is J Law-style systematized swing +
strict risk, because it matches our timeframe and data.

---

## 6. Mindset rules (the "prayer list")

`[VERIFIED]` J Law wrote thousands of trading rules into his phone and reviewed
them daily like scripture, to push discipline into the subconscious. Our
equivalent is this list + the journal. Starter rules:

1. No entry without a predefined stop and position size.
2. Never move a stop *away* from price to avoid being stopped out.
3. Size down in high-volatility / high-VIX regimes.
4. Do not fight the regime: know if the market favors trend or mean-reversion
   today (Stage 5 regime banner).
5. One losing thesis, one exit. No revenge trades, no doubling down to "get even."
6. Review every closed trade: why it won or lost, and which rule it obeyed or
   broke (Stage 2b journal).
7. Theory understood ≠ executed. Judge the system by realized behavior, not
   backtest hope.

---

## 7. How this connects to the code

| Concept | Where it lives |
|---|---|
| Five-pillar timing score | `backend/agents/signal_pillars.py` |
| Entry threshold (default 60) | `backend/agents/recommender.py:18` |
| Regime-adjusted threshold/weights | `backend/agents/market_regime.py` (Stage 5) |
| Position sizing / stops / Kelly | `backend/agents/portfolio_manager.py` |
| Risk metrics (VaR/Sharpe/beta) | `backend/agents/risk_analyzer.py` (Stage 6) |
| Expectancy validation | `backend/backtesting/` (Stage 4) |
| Self-reflection loop | `TRADE_JOURNAL.md` (Stage 2b) |

---

## 8. Changelog (append as the brain evolves)

- 2026-08-21 — Initial brain seeded. Sources: J Law public interview + site
  `[VERIFIED]`; a2ky9 orderflow profile `[VERIFIED, scope-limited]`; classic
  masters and quant frameworks `[CLASSIC]/[PRINCIPLE]`. Mapped philosophy onto
  existing five-pillar engine and portfolio manager.
