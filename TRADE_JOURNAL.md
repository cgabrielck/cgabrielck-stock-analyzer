# TRADE_JOURNAL — Self-Learning Loop

This is the reflective append-only ledger where every significant trade decision
is logged, analyzed, and learned from. The goal is **continuous improvement**:
understand *why* a recommendation won or lost, which philosophy rule it obeyed or
violated, and how the system should adapt.

"Theory understood ≠ executed. Judge the system by realized behavior, not
backtest hope." — This journal *is* that judgment.

---

## How to use this journal

1. **Before a position closes** (or when you run a manual ranking/recommendation):
   log the *thesis* — why the score favored entry, which pillars dominated,
   what the regime was, and the expected edge.
2. **After the position closes** (win, loss, or stop): log the *outcome* — actual
   return, how the exit was triggered, and which assumptions held or broke.
3. **Reflection**: did the system obey or violate a rule from
   `TRADING_PHILOSOPHY.md`? If a rule was broken, by code or by discretion, flag
   it. If the outcome reveals a blind spot, propose a concrete fix.
4. **Changelog**: if the reflection leads to a change in scoring weights, entry
   threshold, stop logic, or a new guardrail, append it to the philosophy
   changelog and link back to this journal entry.

**Append newest entries at the bottom.** Keep entries concise: 5–10 lines per
trade, structured as Thesis / Outcome / Reflection / Action.

---

## Entry format

```
### [YYYY-MM-DD] Ticker: SYMBOL | Direction: LONG/SHORT | Entry: $XX.XX

**Thesis**:
- Five-pillar score: XX.X (coverage: X.XX)
  - Dominant: pillar_name (score XX, reason)
  - Weak: pillar_name (score YY, reason)
- Regime: BULL/BEAR/NEUTRAL | VIX: XX | Entry threshold: XX
- Expected edge: [e.g., mean-reversion above SMA200, RSI oversold, rising volume]
- Position size: X% | Stop: $XX.XX (beta-based -X%) | Target: $XX.XX (+X%)

**Outcome** (close date: YYYY-MM-DD):
- Exit: $XX.XX | P&L: +X.X% / -X.X% | Exit trigger: [stop/target/time/manual]
- Hold period: X days
- What happened: [1-2 sentence narrative: did the thesis play out?]

**Reflection**:
- Rule obeyed/violated: [reference TRADING_PHILOSOPHY.md section/rule number]
- What worked: [which pillar/signal was prescient]
- What failed: [which assumption broke, which data was stale/wrong]
- Blind spot: [if outcome revealed a gap in the system, state it explicitly]

**Action**:
- [None | Code fix | Threshold adjust | New guardrail | Philosophy update]
- [If action taken, link to commit SHA or PROGRESS_LOG entry]
```

---

## Aggregate learnings (updated periodically)

This section distills patterns across multiple trades. Update it monthly or
after 20+ closed trades.

### Win-pattern commonalities
- [e.g., "High momentum pillar + rising 200 SMA in BULL regime → 75% win rate"]

### Loss-pattern commonalities
- [e.g., "Mean-reversion entries in BEAR regime with falling 200 SMA → 60% loss rate"]

### Rule violations and their cost
- [e.g., "3 trades ignored stop discipline → -25% total loss → reinforces Phil §1"]

### Proposed system changes (from reflection)
- [e.g., "Disable mean-reversion pillar entirely in BEAR regime → Stage 5 task"]

---

## Trade log

*(Append entries below in reverse chronological order: newest at bottom)*

---

### [2026-08-21] Meta-entry: Journal initialized

**Purpose**: This journal fulfills the user's requirement that I "reflect and
learn from each trade, understand why I win and lose." It operationalizes J Law's
self-observation practice and the "fewer mistakes" prime directive.

**Action**: Integrated into Stage 2. From now on, every recommendation that
converts to a real or paper trade should append a thesis entry here. Every
closed trade should append an outcome + reflection.

**Note**: Backtest (Stage 4) will generate synthetic journal entries for
historical trades to seed the learning loop with out-of-sample data.

---

<!-- Future trade entries go here -->
