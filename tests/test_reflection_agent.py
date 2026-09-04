"""
Tests for reflection_agent.py — self-learning knowledge base.

Covers:
- Thesis capture from recommendation data
- Outcome logging with P&L calculation
- LLM reflection (mocked)
- Learning synthesis aggregator (mocked)
- Persistence (reflections.json, TRADE_JOURNAL.md append)
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from agents import reflection_agent
from agents.reflection_agent import (
    capture_thesis,
    log_outcome,
    reflect_on_trade,
    synthesize_learnings,
    save_reflection,
    get_reflection_summary,
    complete_trade_cycle,
    format_trade_entry_md,
    append_synthesis_to_philosophy,
)


@pytest.fixture
def temp_data_dir(monkeypatch):
    """Patch DATA_DIR to a temporary directory for isolated tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(reflection_agent, "DATA_DIR", tmpdir)
        monkeypatch.setattr(reflection_agent, "REFLECTIONS_PATH", os.path.join(tmpdir, "reflections.json"))
        yield tmpdir


@pytest.fixture
def temp_philosophy(monkeypatch):
    """Create a temporary TRADING_PHILOSOPHY.md."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("""# Trading Philosophy

## Core Rules
1. Always follow the five-pillar framework
2. Respect stop losses
3. Position size via Kelly Criterion

## Changelog
""")
        philosophy_path = f.name
    
    monkeypatch.setattr(reflection_agent, "PHILOSOPHY_PATH", philosophy_path)
    yield philosophy_path
    os.unlink(philosophy_path)


@pytest.fixture
def temp_journal_md(monkeypatch):
    """Create a temporary TRADE_JOURNAL.md."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# Trade Journal\n\n")
        journal_path = f.name
    
    monkeypatch.setattr(reflection_agent, "JOURNAL_MD_PATH", journal_path)
    yield journal_path
    os.unlink(journal_path)


@pytest.fixture
def sample_recommendation():
    """A realistic recommendation dict from recommender.py."""
    return {
        "ticker": "AAPL",
        "longName": "Apple Inc.",
        "sector": "Technology",
        "total_score": 82.5,
        "risk_adjusted_score": 78.3,
        "llm_score": 85.0,
        "llm_reasoning": "Strong brand moat, consistent buybacks, ecosystem lock-in.",
        "signal_pillars": {
            "score": 80.0,
            "coverage": 0.92,
            "pillars": {
                "trend": {"score": 90, "reason": "Price > SMA50 > SMA200, uptrend confirmed"},
                "momentum": {"score": 85, "reason": "RSI 65, bullish but not overbought"},
                "volume": {"score": 75, "reason": "Volume above 20-day avg"},
                "volatility": {"score": 70, "reason": "Historical vol 18%, manageable"},
                "mean_reversion": {"score": 60, "reason": "Near upper Bollinger, some mean reversion risk"},
            },
        },
        "target_mean_price": 185.0,
        "beta": 1.15,
        "reasoning": "Breakout above SMA200 with strong volume, technical setup favorable.",
    }


# ============================================================================
# 1. THESIS CAPTURE TESTS
# ============================================================================

def test_capture_thesis_basic(sample_recommendation):
    """Test that capture_thesis extracts the right fields."""
    thesis = capture_thesis(
        ticker="AAPL",
        entry_price=175.50,
        recommendation=sample_recommendation,
        position_size_pct=0.08,
        stop_price=165.0,
        target_price=185.0,
    )
    
    assert thesis["ticker"] == "AAPL"
    assert thesis["entry_price"] == 175.50
    assert thesis["direction"] == "LONG"
    assert thesis["five_pillar_score"] == 80.0
    assert thesis["five_pillar_coverage"] == 0.92
    assert thesis["dominant_pillar"]["name"] == "trend"
    assert thesis["dominant_pillar"]["score"] == 90
    assert thesis["weak_pillar"]["name"] == "mean_reversion"
    assert thesis["weak_pillar"]["score"] == 60
    assert thesis["total_score"] == 82.5
    assert thesis["risk_adjusted_score"] == 78.3
    assert thesis["llm_score"] == 85.0
    assert "Strong brand moat" in thesis["llm_reasoning"]
    assert thesis["position_size_pct"] == 8.0
    assert thesis["stop_price"] == 165.0
    assert thesis["target_price"] == 185.0


def test_capture_thesis_missing_pillars():
    """Test thesis capture when signal_pillars is missing."""
    rec = {
        "ticker": "TSLA",
        "total_score": 70.0,
        "risk_adjusted_score": 65.0,
    }
    
    thesis = capture_thesis(
        ticker="TSLA",
        entry_price=250.0,
        recommendation=rec,
    )
    
    assert thesis["ticker"] == "TSLA"
    assert thesis["five_pillar_score"] == 0
    assert thesis["dominant_pillar"]["name"] == "unknown"
    assert thesis["weak_pillar"]["name"] == "unknown"


# ============================================================================
# 2. OUTCOME LOGGING TESTS
# ============================================================================

def test_log_outcome_win():
    """Test outcome logging for a winning trade."""
    entry_date = (datetime.now() - timedelta(days=10)).isoformat()
    
    outcome = log_outcome(
        ticker="AAPL",
        exit_price=185.0,
        exit_trigger="target",
        entry_date=entry_date,
        entry_price=175.0,
        narrative="Hit profit target, exited cleanly.",
    )
    
    assert outcome["ticker"] == "AAPL"
    assert outcome["exit_price"] == 185.0
    assert outcome["exit_trigger"] == "target"
    assert outcome["pnl_pct"] == pytest.approx(5.71, abs=0.01)  # (185-175)/175 * 100
    assert outcome["hold_days"] == 10
    assert "profit target" in outcome["narrative"]


def test_log_outcome_loss():
    """Test outcome logging for a losing trade (stop hit)."""
    entry_date = (datetime.now() - timedelta(days=3)).isoformat()
    
    outcome = log_outcome(
        ticker="NVDA",
        exit_price=450.0,
        exit_trigger="stop",
        entry_date=entry_date,
        entry_price=500.0,
        narrative="Stop loss triggered after earnings miss.",
    )
    
    assert outcome["ticker"] == "NVDA"
    assert outcome["pnl_pct"] == pytest.approx(-10.0, abs=0.01)
    assert outcome["exit_trigger"] == "stop"
    assert outcome["hold_days"] == 3


# ============================================================================
# 3. LLM REFLECTION TESTS (MOCKED)
# ============================================================================

def test_reflect_on_trade_success(sample_recommendation, temp_philosophy):
    """Test LLM reflection on a winning trade (mocked LLM)."""
    thesis = capture_thesis(
        ticker="AAPL",
        entry_price=175.0,
        recommendation=sample_recommendation,
        position_size_pct=0.08,
        stop_price=165.0,
        target_price=185.0,
    )
    
    outcome = log_outcome(
        ticker="AAPL",
        exit_price=185.0,
        exit_trigger="target",
        entry_date=thesis["entry_date"],
        entry_price=175.0,
        narrative="Hit target cleanly.",
    )
    
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "rule_compliance": "obeyed",
        "rule_reference": "N/A",
        "what_worked": "Trend pillar was prescient; breakout above SMA200 played out.",
        "what_failed": "N/A",
        "blind_spot": "None",
        "proposed_action": "None",
        "action_detail": "N/A",
    })
    
    with patch("agents.reflection_agent._get_client") as mock_get_client, \
         patch("agents.reflection_agent._create_completion", return_value=mock_response):
        mock_get_client.return_value = MagicMock()
        
        reflection = reflect_on_trade(thesis, outcome)
    
    assert reflection is not None
    assert reflection["ticker"] == "AAPL"
    assert reflection["rule_compliance"] == "obeyed"
    assert "Trend pillar" in reflection["what_worked"]
    assert reflection["pnl_pct"] == pytest.approx(5.71, abs=0.01)


def test_reflect_on_trade_loss(sample_recommendation, temp_philosophy):
    """Test LLM reflection on a losing trade (mocked LLM)."""
    thesis = capture_thesis(
        ticker="TSLA",
        entry_price=250.0,
        recommendation=sample_recommendation,
        position_size_pct=0.05,
        stop_price=225.0,
    )
    
    outcome = log_outcome(
        ticker="TSLA",
        exit_price=225.0,
        exit_trigger="stop",
        entry_date=thesis["entry_date"],
        entry_price=250.0,
        narrative="Stop hit after sudden macro selloff.",
    )
    
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "rule_compliance": "obeyed",
        "rule_reference": "N/A",
        "what_worked": "Stop loss protected capital as designed.",
        "what_failed": "Macro regime shift not detected; VIX spiked from 15 to 30 overnight.",
        "blind_spot": "No real-time VIX monitoring for regime changes.",
        "proposed_action": "New guardrail",
        "action_detail": "Add daily VIX check; pause entries if VIX > 25.",
    })
    
    with patch("agents.reflection_agent._get_client") as mock_get_client, \
         patch("agents.reflection_agent._create_completion", return_value=mock_response):
        mock_get_client.return_value = MagicMock()
        
        reflection = reflect_on_trade(thesis, outcome)
    
    assert reflection is not None
    assert reflection["rule_compliance"] == "obeyed"
    assert "Stop loss protected" in reflection["what_worked"]
    assert "VIX" in reflection["what_failed"]
    assert reflection["proposed_action"] == "New guardrail"


def test_reflect_on_trade_no_llm(sample_recommendation):
    """Test that reflection returns None when LLM unavailable."""
    thesis = capture_thesis("AAPL", 175.0, sample_recommendation)
    outcome = log_outcome("AAPL", 185.0, "target", thesis["entry_date"], 175.0)
    
    with patch("agents.reflection_agent._get_client", return_value=None):
        reflection = reflect_on_trade(thesis, outcome)
    
    assert reflection is None


# ============================================================================
# 4. LEARNING SYNTHESIS TESTS (MOCKED)
# ============================================================================

def test_synthesize_learnings_success(temp_data_dir, temp_philosophy):
    """Test learning synthesis across multiple reflections (mocked LLM)."""
    # Create 6 mock reflections
    reflections = [
        {"ticker": "AAPL", "pnl_pct": 5.0, "rule_compliance": "obeyed", "what_worked": "Trend", "what_failed": "N/A", "blind_spot": "None", "proposed_action": "None"},
        {"ticker": "MSFT", "pnl_pct": 3.5, "rule_compliance": "obeyed", "what_worked": "Momentum", "what_failed": "N/A", "blind_spot": "None", "proposed_action": "None"},
        {"ticker": "NVDA", "pnl_pct": -10.0, "rule_compliance": "obeyed", "what_worked": "Stop", "what_failed": "Macro shift", "blind_spot": "No VIX check", "proposed_action": "New guardrail"},
        {"ticker": "TSLA", "pnl_pct": -8.0, "rule_compliance": "violated", "what_worked": "N/A", "what_failed": "Entry above SMA200", "blind_spot": "Weak trend filter", "proposed_action": "Threshold adjust"},
        {"ticker": "GOOGL", "pnl_pct": 4.0, "rule_compliance": "obeyed", "what_worked": "Volume breakout", "what_failed": "N/A", "blind_spot": "None", "proposed_action": "None"},
        {"ticker": "AMZN", "pnl_pct": 6.0, "rule_compliance": "obeyed", "what_worked": "Five-pillar coverage high", "what_failed": "N/A", "blind_spot": "None", "proposed_action": "None"},
    ]
    
    # Save reflections
    with open(os.path.join(temp_data_dir, "reflections.json"), "w") as f:
        json.dump(reflections, f)
    
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "win_patterns": ["Wins cluster in high five-pillar coverage", "Volume breakouts worked well"],
        "loss_patterns": ["Losses from macro regime shifts", "Weak trend filter violations"],
        "rule_violations": ["1 entry above SMA200 without confirmation"],
        "blind_spots": ["No real-time VIX monitoring", "Trend filter too permissive"],
        "proposed_adjustments": [
            {
                "type": "guardrail",
                "target": "regime detection",
                "current": "None",
                "proposed": "Daily VIX check > 25 → pause entries",
                "rationale": "NVDA loss from VIX spike undetected",
            },
            {
                "type": "threshold",
                "target": "trend pillar",
                "current": "Price > SMA200",
                "proposed": "Price > SMA200 AND SMA50 > SMA200",
                "rationale": "TSLA loss from weak trend confirmation",
            },
        ],
        "confidence": "medium",
    })
    
    with patch("agents.reflection_agent._get_client") as mock_get_client, \
         patch("agents.reflection_agent._create_completion", return_value=mock_response):
        mock_get_client.return_value = MagicMock()
        
        synthesis = synthesize_learnings()
    
    assert synthesis is not None
    assert len(synthesis["win_patterns"]) == 2
    assert "five-pillar" in synthesis["win_patterns"][0].lower() or "volume" in synthesis["win_patterns"][1].lower()
    assert len(synthesis["proposed_adjustments"]) == 2
    assert synthesis["confidence"] == "medium"
    assert synthesis["num_trades_analyzed"] == 6


def test_synthesize_learnings_too_few_trades(temp_data_dir):
    """Test that synthesis returns None when too few reflections."""
    reflections = [
        {"ticker": "AAPL", "pnl_pct": 5.0, "rule_compliance": "obeyed", "what_worked": "Trend", "what_failed": "N/A", "blind_spot": "None", "proposed_action": "None"},
    ]
    
    with open(os.path.join(temp_data_dir, "reflections.json"), "w") as f:
        json.dump(reflections, f)
    
    with patch("agents.reflection_agent._get_client") as mock_get_client:
        mock_get_client.return_value = MagicMock()
        
        synthesis = synthesize_learnings(min_trades=5)
    
    assert synthesis is None


# ============================================================================
# 5. PERSISTENCE TESTS
# ============================================================================

def test_save_and_load_reflection(temp_data_dir):
    """Test saving and loading reflections from JSON."""
    reflection = {
        "ticker": "AAPL",
        "reflection_date": datetime.now().isoformat(),
        "pnl_pct": 5.0,
        "rule_compliance": "obeyed",
        "what_worked": "Trend pillar",
        "what_failed": "N/A",
        "blind_spot": "None",
        "proposed_action": "None",
    }
    
    save_reflection(reflection)
    
    from agents.reflection_agent import _load_reflections
    loaded = _load_reflections()
    
    assert len(loaded) == 1
    assert loaded[0]["ticker"] == "AAPL"
    assert loaded[0]["pnl_pct"] == 5.0


def test_get_reflection_summary(temp_data_dir):
    """Test reflection summary stats."""
    reflections = [
        {"ticker": "AAPL", "pnl_pct": 5.0, "rule_compliance": "obeyed"},
        {"ticker": "MSFT", "pnl_pct": 3.0, "rule_compliance": "obeyed"},
        {"ticker": "NVDA", "pnl_pct": -10.0, "rule_compliance": "obeyed"},
        {"ticker": "TSLA", "pnl_pct": -5.0, "rule_compliance": "violated"},
        {"ticker": "GOOGL", "pnl_pct": 4.0, "rule_compliance": "obeyed"},
    ]
    
    with open(os.path.join(temp_data_dir, "reflections.json"), "w") as f:
        json.dump(reflections, f)
    
    summary = get_reflection_summary()
    
    assert summary["total_trades"] == 5
    assert summary["wins"] == 3
    assert summary["losses"] == 2
    assert summary["win_rate"] == 60.0
    assert summary["avg_pnl"] == pytest.approx(-0.6, abs=0.1)  # (5+3-10-5+4)/5
    assert summary["rule_violations"] == 1


# ============================================================================
# 6. MARKDOWN FORMATTING TESTS
# ============================================================================

def test_format_trade_entry_md(sample_recommendation):
    """Test markdown formatting of a trade entry."""
    thesis = capture_thesis("AAPL", 175.0, sample_recommendation, position_size_pct=0.08, stop_price=165.0, target_price=185.0)
    outcome = log_outcome("AAPL", 185.0, "target", thesis["entry_date"], 175.0, "Hit target cleanly.")
    
    reflection = {
        "rule_compliance": "obeyed",
        "rule_reference": "N/A",
        "what_worked": "Trend pillar prescient.",
        "what_failed": "N/A",
        "blind_spot": "None",
        "proposed_action": "None",
        "action_detail": "N/A",
    }
    
    md_entry = format_trade_entry_md(thesis, outcome, reflection)
    
    assert "### [" in md_entry
    assert "AAPL" in md_entry
    assert "$175.0" in md_entry
    assert "Five-pillar score: 80.0" in md_entry
    assert "Dominant: trend" in md_entry
    assert "P&L: +5.71%" in md_entry
    assert "Rule obeyed" in md_entry
    assert "Trend pillar prescient" in md_entry


def test_append_to_journal_md(temp_journal_md):
    """Test appending a formatted entry to TRADE_JOURNAL.md."""
    from agents.reflection_agent import _append_to_journal_md
    
    entry = "### [2024-01-15] AAPL | LONG | Entry $175.0\n\n**Thesis**: Test entry\n"
    _append_to_journal_md(entry)
    
    with open(temp_journal_md, encoding="utf-8") as f:
        content = f.read()
    
    assert "AAPL" in content
    assert "Test entry" in content


def test_append_synthesis_to_philosophy(temp_philosophy):
    """Test appending a synthesis to TRADING_PHILOSOPHY.md changelog."""
    synthesis = {
        "synthesis_date": datetime.now().isoformat(),
        "num_trades_analyzed": 10,
        "win_patterns": ["High coverage wins", "Volume breakouts"],
        "loss_patterns": ["Macro regime shifts"],
        "proposed_adjustments": [
            {
                "type": "guardrail",
                "target": "VIX",
                "current": "None",
                "proposed": "VIX > 25 → pause",
                "rationale": "Avoid macro selloffs",
            },
        ],
        "confidence": "high",
    }
    
    append_synthesis_to_philosophy(synthesis)
    
    with open(temp_philosophy, encoding="utf-8") as f:
        content = f.read()
    
    assert "Learning Synthesis (10 trades)" in content
    assert "Win patterns" in content
    assert "High coverage wins" in content
    assert "GUARDRAIL: VIX" in content
    assert "confidence: high" in content


# ============================================================================
# 7. COMPLETE TRADE CYCLE TEST
# ============================================================================

def test_complete_trade_cycle(sample_recommendation, temp_data_dir, temp_journal_md, temp_philosophy):
    """Test the full trade cycle: thesis → outcome → reflect → save → append MD."""
    thesis = capture_thesis("AAPL", 175.0, sample_recommendation, position_size_pct=0.08, stop_price=165.0, target_price=185.0)
    outcome = log_outcome("AAPL", 185.0, "target", thesis["entry_date"], 175.0, "Hit target cleanly.")
    
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "rule_compliance": "obeyed",
        "rule_reference": "N/A",
        "what_worked": "Trend pillar prescient.",
        "what_failed": "N/A",
        "blind_spot": "None",
        "proposed_action": "None",
        "action_detail": "N/A",
    })
    
    with patch("agents.reflection_agent._get_client") as mock_get_client, \
         patch("agents.reflection_agent._create_completion", return_value=mock_response):
        mock_get_client.return_value = MagicMock()
        
        reflection = complete_trade_cycle(thesis, outcome, append_to_markdown=True)
    
    # Check reflection was returned
    assert reflection is not None
    assert reflection["ticker"] == "AAPL"
    
    # Check reflection was saved to JSON
    from agents.reflection_agent import _load_reflections
    saved = _load_reflections()
    assert len(saved) == 1
    assert saved[0]["ticker"] == "AAPL"
    
    # Check markdown was appended
    with open(temp_journal_md, encoding="utf-8") as f:
        md_content = f.read()
    assert "AAPL" in md_content
    assert "Trend pillar prescient" in md_content
