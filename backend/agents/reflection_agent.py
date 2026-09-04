"""
Reflection Agent — Self-Learning Knowledge Base

This module captures the thesis at trade entry, logs outcomes at exit, and uses
the LLM to reflect on wins/losses. It forms a feedback loop:
  1. Capture → what was the scoring thesis, regime, expected edge
  2. Outcome → what happened, P&L, exit trigger
  3. Reflect → LLM analyzes what worked/failed, philosophy rule compliance
  4. Learn → periodically synthesize patterns, propose system adjustments

Integrates with:
- portfolio_manager.py: extend log_trade() to capture thesis+outcome
- llm_agent.py: reuse LLM client for reflection prompts
- TRADING_PHILOSOPHY.md: source of truth for rules
- TRADE_JOURNAL.md: append-only ledger for human + code review
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.llm_agent import _get_client, _create_completion, get_model_for_task
from utils.constants import DATA_DIR


REFLECTIONS_PATH = os.path.join(DATA_DIR, "reflections.json")
PHILOSOPHY_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "TRADING_PHILOSOPHY.md")
JOURNAL_MD_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "TRADE_JOURNAL.md")


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_reflections() -> List[Dict[str, Any]]:
    """Load all past reflections from data/reflections.json."""
    _ensure_data_dir()
    if os.path.exists(REFLECTIONS_PATH):
        try:
            with open(REFLECTIONS_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_reflections(reflections: List[Dict[str, Any]]) -> None:
    """Persist reflections to disk."""
    _ensure_data_dir()
    with open(REFLECTIONS_PATH, "w") as f:
        json.dump(reflections, f, indent=2, ensure_ascii=False)


def _load_philosophy() -> str:
    """Load TRADING_PHILOSOPHY.md as a string for context."""
    if os.path.exists(PHILOSOPHY_PATH):
        with open(PHILOSOPHY_PATH, encoding="utf-8") as f:
            return f.read()
    return ""


def _append_to_journal_md(entry: str) -> None:
    """Append a formatted trade entry to TRADE_JOURNAL.md."""
    with open(JOURNAL_MD_PATH, "a", encoding="utf-8") as f:
        f.write("\n" + entry + "\n")


# ============================================================================
# 1. THESIS CAPTURE
# ============================================================================

def capture_thesis(
    ticker: str,
    entry_price: float,
    recommendation: Dict[str, Any],
    regime: Optional[Dict[str, Any]] = None,
    position_size_pct: float = 0.0,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Serialize the scoring thesis at entry time.
    
    Args:
        ticker: stock symbol
        entry_price: actual entry price
        recommendation: the full recommendation dict (contains scores, pillars, etc.)
        regime: optional market regime context (BULL/BEAR/NEUTRAL, VIX, etc.)
        position_size_pct: portfolio weight (0-1)
        stop_price: stop loss price
        target_price: profit target price
    
    Returns:
        A dict ready to be stored in the trade journal.
    """
    pillars = recommendation.get("signal_pillars", {})
    pillar_scores = pillars.get("pillars", {})
    
    # Identify dominant and weak pillars
    sorted_pillars = sorted(pillar_scores.items(), key=lambda x: x[1].get("score", 0), reverse=True)
    dominant = sorted_pillars[0] if sorted_pillars else ("unknown", {"score": 0, "reason": ""})
    weak = sorted_pillars[-1] if sorted_pillars else ("unknown", {"score": 0, "reason": ""})
    
    thesis = {
        "ticker": ticker,
        "entry_date": datetime.now().isoformat(),
        "entry_price": round(entry_price, 2),
        "direction": "LONG",  # current system only does long
        "five_pillar_score": round(pillars.get("score", 0), 1),
        "five_pillar_coverage": round(pillars.get("coverage", 0), 2),
        "dominant_pillar": {
            "name": dominant[0],
            "score": round(dominant[1].get("score", 0), 1),
            "reason": dominant[1].get("reason", ""),
        },
        "weak_pillar": {
            "name": weak[0],
            "score": round(weak[1].get("score", 0), 1),
            "reason": weak[1].get("reason", ""),
        },
        "total_score": round(recommendation.get("total_score", 0), 1),
        "risk_adjusted_score": round(recommendation.get("risk_adjusted_score", 0), 1),
        "llm_score": round(recommendation.get("llm_score", 0), 1) if recommendation.get("llm_score") else None,
        "llm_reasoning": recommendation.get("llm_reasoning"),
        "regime": regime or {},
        "expected_edge": recommendation.get("reasoning", ""),
        "position_size_pct": round(position_size_pct * 100, 1),
        "stop_price": round(stop_price, 2) if stop_price else None,
        "target_price": round(target_price, 2) if target_price else None,
    }
    
    return thesis


# ============================================================================
# 2. OUTCOME LOGGING
# ============================================================================

def log_outcome(
    ticker: str,
    exit_price: float,
    exit_trigger: str,
    entry_date: str,
    entry_price: float,
    narrative: str = "",
) -> Dict[str, Any]:
    """
    Record the outcome of a closed position.
    
    Args:
        ticker: stock symbol
        exit_price: actual exit price
        exit_trigger: "stop" | "target" | "time" | "manual" | "rebalance"
        entry_date: ISO timestamp of entry
        entry_price: original entry price
        narrative: 1-2 sentence summary of what happened
    
    Returns:
        Outcome dict with P&L, hold period, etc.
    """
    entry_dt = datetime.fromisoformat(entry_date)
    exit_dt = datetime.now()
    hold_days = (exit_dt - entry_dt).days
    
    pnl_pct = ((exit_price / entry_price) - 1) * 100 if entry_price > 0 else 0
    
    outcome = {
        "ticker": ticker,
        "exit_date": exit_dt.isoformat(),
        "exit_price": round(exit_price, 2),
        "exit_trigger": exit_trigger,
        "pnl_pct": round(pnl_pct, 2),
        "hold_days": hold_days,
        "narrative": narrative,
    }
    
    return outcome


# ============================================================================
# 3. LLM REFLECTION
# ============================================================================

_REFLECTION_SYSTEM_PROMPT = """You are a trading system analyst. Your job is to reflect on closed trades, identify what worked and what failed, and ensure the system follows its own rules.

You will receive:
- The thesis: why the system entered this trade (scoring pillars, expected edge, regime)
- The outcome: P&L, exit trigger, hold period, what happened
- The trading philosophy: the rules this system is supposed to follow

Your response must be in JSON format:
{
  "rule_compliance": "obeyed" | "violated",
  "rule_reference": "section/rule number from philosophy, if violated",
  "what_worked": "which pillar/signal was prescient (1-2 sentences)",
  "what_failed": "which assumption broke, stale data, or regime shift (1-2 sentences, 'N/A' if win)",
  "blind_spot": "gap in the system this outcome revealed (1 sentence, 'None' if no gap)",
  "proposed_action": "None" | "Threshold adjust" | "Weight shift" | "New guardrail" | "Data quality fix",
  "action_detail": "specific recommendation (e.g., 'increase stop from 10% to 12%', 'add sector rotation check')"
}

Be concise, honest, and actionable. If the trade was a win but followed a flawed process, flag it. If it was a loss but followed the rules perfectly, say so."""


def reflect_on_trade(
    thesis: Dict[str, Any],
    outcome: Dict[str, Any],
    philosophy: Optional[str] = None,
    lang: str = "en",
) -> Optional[Dict[str, Any]]:
    """
    Use the LLM to reflect on a single closed trade.
    
    Args:
        thesis: dict from capture_thesis()
        outcome: dict from log_outcome()
        philosophy: optional TRADING_PHILOSOPHY.md text (will load if not provided)
        lang: language code (currently only 'en' for reflection)
    
    Returns:
        Reflection dict with rule_compliance, what_worked, what_failed, blind_spot, proposed_action.
        Returns None if LLM unavailable or error.
    """
    client = _get_client()
    if not client:
        return None
    
    if philosophy is None:
        philosophy = _load_philosophy()
    
    # Format the trade for the LLM
    trade_summary = f"""
**Ticker**: {thesis['ticker']}
**Entry**: {thesis['entry_date']} @ ${thesis['entry_price']}
**Exit**: {outcome['exit_date']} @ ${outcome['exit_price']}
**P&L**: {outcome['pnl_pct']:+.2f}%
**Hold period**: {outcome['hold_days']} days
**Exit trigger**: {outcome['exit_trigger']}

**Thesis**:
- Five-pillar score: {thesis['five_pillar_score']} (coverage: {thesis['five_pillar_coverage']})
- Dominant: {thesis['dominant_pillar']['name']} (score {thesis['dominant_pillar']['score']}, reason: {thesis['dominant_pillar']['reason']})
- Weak: {thesis['weak_pillar']['name']} (score {thesis['weak_pillar']['score']})
- Total score: {thesis['total_score']}, Risk-adjusted: {thesis['risk_adjusted_score']}
- LLM score: {thesis.get('llm_score', 'N/A')}
- Position size: {thesis['position_size_pct']}%
- Stop: ${thesis.get('stop_price', 'N/A')} | Target: ${thesis.get('target_price', 'N/A')}
- Expected edge: {thesis['expected_edge']}

**Outcome narrative**: {outcome['narrative']}

**Trading Philosophy** (abbreviated):
{philosophy[:3000]}
"""
    
    user_prompt = f"Reflect on this trade and return your analysis in JSON format:\n\n{trade_summary}"
    
    try:
        response = _create_completion(
            client,
            task="reflection",
            messages=[
                {"role": "system", "content": _REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        
        content = response.choices[0].message.content
        reflection = json.loads(content)
        
        # Attach metadata
        reflection["ticker"] = thesis["ticker"]
        reflection["reflection_date"] = datetime.now().isoformat()
        reflection["pnl_pct"] = outcome["pnl_pct"]
        
        return reflection
        
    except Exception as e:
        print(f"[reflection_agent] LLM reflection failed for {thesis['ticker']}: {e}")
        return None


# ============================================================================
# 4. LEARNING AGGREGATOR
# ============================================================================

_SYNTHESIS_SYSTEM_PROMPT = """You are a trading system meta-analyst. Your job is to synthesize patterns across multiple trade reflections and propose concrete system improvements.

You will receive:
- A list of recent trade reflections (wins, losses, rule violations)
- The current trading philosophy

Your response must be in JSON format:
{
  "win_patterns": ["pattern 1", "pattern 2", ...],
  "loss_patterns": ["pattern 1", "pattern 2", ...],
  "rule_violations": ["violation type + frequency", ...],
  "blind_spots": ["systemic gap 1", "systemic gap 2", ...],
  "proposed_adjustments": [
    {
      "type": "weight" | "threshold" | "guardrail" | "data",
      "target": "which pillar/module/threshold",
      "current": "current value",
      "proposed": "new value",
      "rationale": "why this change (reference specific trades)"
    },
    ...
  ],
  "confidence": "low" | "medium" | "high"
}

Be concrete. If you see 3 losses from entries above SMA200 that immediately reversed, propose a stricter trend filter. If you see wins clustered in high-volume breakouts, propose increasing the volume pillar weight. Confidence depends on sample size and consistency."""


def synthesize_learnings(
    reflections: Optional[List[Dict[str, Any]]] = None,
    philosophy: Optional[str] = None,
    min_trades: int = 5,
    lang: str = "en",
) -> Optional[Dict[str, Any]]:
    """
    Aggregate multiple trade reflections to identify patterns and propose system changes.
    
    Args:
        reflections: list of reflection dicts (will load from disk if None)
        philosophy: TRADING_PHILOSOPHY.md text (will load if None)
        min_trades: minimum number of reflections required to synthesize
        lang: language code
    
    Returns:
        Synthesis dict with win_patterns, loss_patterns, proposed_adjustments.
        Returns None if too few trades or LLM unavailable.
    """
    client = _get_client()
    if not client:
        return None
    
    if reflections is None:
        reflections = _load_reflections()
    
    if len(reflections) < min_trades:
        return None
    
    if philosophy is None:
        philosophy = _load_philosophy()
    
    # Take the most recent N reflections (last 20 or all if fewer)
    recent = reflections[-20:]
    
    # Format for the LLM
    reflection_summary = "\n\n".join([
        f"Trade {i+1}: {r['ticker']} | P&L {r['pnl_pct']:+.2f}% | "
        f"Compliance: {r['rule_compliance']} | "
        f"Worked: {r['what_worked']} | "
        f"Failed: {r['what_failed']} | "
        f"Blind spot: {r['blind_spot']} | "
        f"Action: {r['proposed_action']} ({r.get('action_detail', '')})"
        for i, r in enumerate(recent)
    ])
    
    user_prompt = f"""Synthesize these {len(recent)} trade reflections and propose system improvements:

{reflection_summary}

**Current Philosophy** (abbreviated):
{philosophy[:2000]}

Return your analysis in JSON format."""
    
    try:
        response = _create_completion(
            client,
            task="strategy",  # use reasoning model for deep synthesis
            messages=[
                {"role": "system", "content": _SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        
        content = response.choices[0].message.content
        synthesis = json.loads(content)
        
        synthesis["synthesis_date"] = datetime.now().isoformat()
        synthesis["num_trades_analyzed"] = len(recent)
        
        return synthesis
        
    except Exception as e:
        print(f"[reflection_agent] Learning synthesis failed: {e}")
        return None


# ============================================================================
# 5. PERSISTENCE & FORMATTING
# ============================================================================

def save_reflection(reflection: Dict[str, Any]) -> None:
    """Append a reflection to data/reflections.json."""
    reflections = _load_reflections()
    reflections.append(reflection)
    _save_reflections(reflections)


def format_trade_entry_md(
    thesis: Dict[str, Any],
    outcome: Dict[str, Any],
    reflection: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Format a completed trade as a markdown entry for TRADE_JOURNAL.md.
    
    Follows the template in TRADE_JOURNAL.md (Thesis / Outcome / Reflection / Action).
    """
    entry_lines = [
        f"### [{thesis['entry_date'][:10]}] Ticker: {thesis['ticker']} | Direction: {thesis['direction']} | Entry: ${thesis['entry_price']}",
        "",
        "**Thesis**:",
        f"- Five-pillar score: {thesis['five_pillar_score']} (coverage: {thesis['five_pillar_coverage']})",
        f"  - Dominant: {thesis['dominant_pillar']['name']} (score {thesis['dominant_pillar']['score']}, reason: {thesis['dominant_pillar']['reason']})",
        f"  - Weak: {thesis['weak_pillar']['name']} (score {thesis['weak_pillar']['score']})",
        f"- Regime: {thesis['regime'].get('state', 'N/A')} | VIX: {thesis['regime'].get('vix', 'N/A')}",
        f"- Expected edge: {thesis['expected_edge'][:150]}...",
        f"- Position size: {thesis['position_size_pct']}% | Stop: ${thesis.get('stop_price', 'N/A')} | Target: ${thesis.get('target_price', 'N/A')}",
        "",
        f"**Outcome** (close date: {outcome['exit_date'][:10]}):",
        f"- Exit: ${outcome['exit_price']} | P&L: {outcome['pnl_pct']:+.2f}% | Exit trigger: {outcome['exit_trigger']}",
        f"- Hold period: {outcome['hold_days']} days",
        f"- What happened: {outcome['narrative']}",
        "",
    ]
    
    if reflection:
        entry_lines.extend([
            "**Reflection**:",
            f"- Rule {reflection['rule_compliance']}: {reflection.get('rule_reference', 'N/A')}",
            f"- What worked: {reflection['what_worked']}",
            f"- What failed: {reflection['what_failed']}",
            f"- Blind spot: {reflection['blind_spot']}",
            "",
            "**Action**:",
            f"- {reflection['proposed_action']}: {reflection.get('action_detail', 'N/A')}",
            "",
        ])
    
    return "\n".join(entry_lines)


def append_synthesis_to_philosophy(synthesis: Dict[str, Any]) -> None:
    """
    Append a learning synthesis to the TRADING_PHILOSOPHY.md changelog section.
    """
    if not os.path.exists(PHILOSOPHY_PATH):
        return
    
    with open(PHILOSOPHY_PATH, encoding="utf-8") as f:
        lines = f.readlines()
    
    # Find the changelog section (should be near the end)
    changelog_idx = None
    for i, line in enumerate(lines):
        if "## Changelog" in line or "## 99." in line:
            changelog_idx = i
            break
    
    if changelog_idx is None:
        # No changelog section, append at the end
        changelog_idx = len(lines)
        lines.append("\n---\n\n## Changelog — Evolution of the System\n\n")
    
    # Format the synthesis as a changelog entry
    entry = f"""
### {synthesis['synthesis_date'][:10]} — Learning Synthesis ({synthesis['num_trades_analyzed']} trades)

**Win patterns**: {', '.join(synthesis['win_patterns']) if synthesis['win_patterns'] else 'None identified yet'}

**Loss patterns**: {', '.join(synthesis['loss_patterns']) if synthesis['loss_patterns'] else 'None identified yet'}

**Proposed adjustments** (confidence: {synthesis['confidence']}):
"""
    
    for adj in synthesis.get('proposed_adjustments', []):
        entry += f"- {adj['type'].upper()}: {adj['target']} — {adj['current']} → {adj['proposed']} ({adj['rationale']})\n"
    
    entry += "\n"
    
    lines.insert(changelog_idx + 2, entry)
    
    with open(PHILOSOPHY_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ============================================================================
# 6. CONVENIENCE WRAPPERS
# ============================================================================

def complete_trade_cycle(
    thesis: Dict[str, Any],
    outcome: Dict[str, Any],
    append_to_markdown: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Full cycle: reflect on a closed trade, save reflection, optionally append to journal MD.
    
    Args:
        thesis: from capture_thesis()
        outcome: from log_outcome()
        append_to_markdown: if True, write formatted entry to TRADE_JOURNAL.md
    
    Returns:
        The reflection dict, or None if LLM unavailable.
    """
    reflection = reflect_on_trade(thesis, outcome)
    
    if reflection:
        save_reflection(reflection)
    
    if append_to_markdown:
        md_entry = format_trade_entry_md(thesis, outcome, reflection)
        _append_to_journal_md(md_entry)
    
    return reflection


def get_recent_reflections(n: int = 10) -> List[Dict[str, Any]]:
    """Load the N most recent reflections."""
    all_reflections = _load_reflections()
    return all_reflections[-n:]


def get_reflection_summary() -> Dict[str, Any]:
    """Summary stats: total trades, win rate, avg P&L, rule violations."""
    reflections = _load_reflections()
    if not reflections:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "rule_violations": 0,
        }
    
    wins = [r for r in reflections if r.get("pnl_pct", 0) > 0]
    losses = [r for r in reflections if r.get("pnl_pct", 0) <= 0]
    violations = [r for r in reflections if r.get("rule_compliance") == "violated"]
    
    total_pnl = sum(r.get("pnl_pct", 0) for r in reflections)
    
    return {
        "total_trades": len(reflections),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(reflections) * 100, 1) if reflections else 0.0,
        "avg_pnl": round(total_pnl / len(reflections), 2) if reflections else 0.0,
        "rule_violations": len(violations),
    }
