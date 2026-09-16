"""Tests for lab citation-try — work without lab packages installed."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture()
def lab_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_INSTRUCTOR", "0")
    monkeypatch.setenv("LAB_FINBERT", "0")
    monkeypatch.setenv("LAB_QUANTSTATS", "0")
    monkeypatch.setenv("LAB_ADVISORY", "0")
    monkeypatch.setattr("backend.lab.instructor_parse.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("backend.lab.finbert_sentiment.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("backend.lab.advisory_debate.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("backend.lab.quantstats_tear.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("backend.lab.ab_metrics.DATA_DIR", str(tmp_path))
    # Paths use Path(DATA_DIR) at import — patch module-level Path constants too
    from backend.lab import instructor_parse, finbert_sentiment, advisory_debate, quantstats_tear

    monkeypatch.setattr(instructor_parse, "_METRICS_PATH", tmp_path / "lab_schema_metrics.json")
    monkeypatch.setattr(finbert_sentiment, "_METRICS_PATH", tmp_path / "lab_sentiment_metrics.json")
    monkeypatch.setattr(advisory_debate, "_METRICS_PATH", tmp_path / "lab_advisory_metrics.json")
    monkeypatch.setattr(advisory_debate, "_BUDGET_PATH", tmp_path / "lab_advisory_budget.json")
    return tmp_path


def test_lab_status_never_orders():
    from backend.lab.flags import lab_status

    s = lab_status()
    assert s["never_places_orders"] is True
    assert "FinRL" in " ".join(s["refused"])


def test_schema_parse_success_and_fail(lab_data_dir, monkeypatch):
    monkeypatch.setenv("LAB_INSTRUCTOR", "1")
    from backend.lab.instructor_parse import parse_scan_llm_text, schema_metrics

    ok = parse_scan_llm_text(
        json.dumps(
            {
                "final_score": 70,
                "reasoning": "x",
                "technical_summary": "y",
                "key_signal": "buy",
            }
        )
    )
    assert ok["lab_schema_ok"] is True
    assert ok["key_signal"] == "bullish"
    bad = parse_scan_llm_text("not-json{")
    assert bad["lab_schema_ok"] is False
    m = schema_metrics()
    assert m["attempts"] >= 2
    assert m["ok"] >= 1


def test_advisory_off_and_on(lab_data_dir, monkeypatch):
    from backend.lab.advisory_debate import run_advisory_debate

    off = run_advisory_debate("AAPL", fund_score=80, top_n_before=["MSFT"])
    assert off["enabled"] is False
    assert off["places_order"] is False

    monkeypatch.setenv("LAB_ADVISORY", "1")
    on = run_advisory_debate("AAPL", fund_score=80, top_n_before=["MSFT", "NVDA"])
    assert on["enabled"] is True
    assert on["places_order"] is False
    assert on["top_n_rewritten"] is True
    assert on["top_n_after_suggestion"][0] == "AAPL"
    for role in on["roles"]:
        assert role["places_order"] is False


def test_finbert_falls_back_to_vader(lab_data_dir, monkeypatch):
    monkeypatch.setenv("LAB_FINBERT", "1")
    from backend.lab.finbert_sentiment import classify_news_sentiment

    label, meta = classify_news_sentiment("Shares surge on strong earnings beat", "")
    assert label in ("positive", "negative", "neutral")
    assert meta["vader"] in ("positive", "negative", "neutral")


def test_tear_stub_without_quantstats(lab_data_dir, monkeypatch):
    monkeypatch.setenv("LAB_QUANTSTATS", "0")
    from backend.lab.quantstats_tear import build_tear_sheet

    out = build_tear_sheet(
        equity_curve=[
            {"date": "2026-01-01", "equity": 100.0},
            {"date": "2026-01-02", "equity": 101.0},
            {"date": "2026-01-03", "equity": 102.0},
        ],
        out_html=lab_data_dir / "tear.html",
    )
    assert out["complete"] is True
    assert out["engine"] == "json_stub"
    assert Path(out["path"]).exists()


def test_ab_report_shape(lab_data_dir):
    from backend.lab.ab_metrics import collect_ab_report, write_ab_report

    r = collect_ab_report()
    assert "schema" in r and "sentiment" in r and "advisory" in r and "tear" in r
    assert "NOT" in r["purpose"] or "not" in r["purpose"].lower()
    path = write_ab_report(lab_data_dir / "lab_ab_report.json")
    assert path.exists()
