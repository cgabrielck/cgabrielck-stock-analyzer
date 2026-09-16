"""Tests for research job cache + fund score lookup used by the paper worker."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import backend.api.research_jobs as rj


def _write_scan(tmp_path: Path, monkeypatch, *, hours_ago: float = 1.0, score: float = 72.0):
    path = tmp_path / "last_scan.json"
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    payload = {
        "ts": ts,
        "top5_tickers": ["AAPL", "MSFT"],
        "rankings": [{"ticker": "AAPL", "rank": 1, "risk_adjusted_score": score}],
        "recommendations": [{"ticker": "AAPL", "risk_adjusted_score": score}],
        "scores_by_ticker": {
            "AAPL": {"risk_adjusted_score": score, "growth_score": 70, "model_score": 71, "sentiment_score": 60},
            "MSFT": {"risk_adjusted_score": 68, "growth_score": 66},
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(rj, "LAST_SCAN_PATH", path)
    return path


def test_latest_scan_backfills_provenance(tmp_path, monkeypatch):
    """Old last_scan.json without provenance still gets chips on read."""
    monkeypatch.setattr("backend.api.provenance.polygon_configured", lambda: False)
    path = tmp_path / "last_scan.json"
    path.write_text(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "recommendations": [{"ticker": "AAPL", "price": 100, "price_source": "yahoo_regular_market"}],
                "rankings": [{"ticker": "AAPL", "rank": 1, "price_source": "yahoo_regular_market"}],
                "top5_tickers": ["AAPL"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rj, "LAST_SCAN_PATH", path)
    scan = rj.latest_scan()
    assert scan["available"] is True
    assert isinstance(scan.get("provenance"), dict)
    assert scan["provenance"].get("as_of")
    assert isinstance(scan["recommendations"][0].get("provenance"), dict)
    assert scan["recommendations"][0]["provenance"]["vendor"] == "yahoo"
    assert isinstance(scan["rankings"][0].get("provenance"), dict)


def test_latest_scan_refreshes_unknown_vendor(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.api.provenance.polygon_configured", lambda: False)
    path = tmp_path / "last_scan.json"
    path.write_text(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "recommendations": [
                    {
                        "ticker": "AAPL",
                        "price": 100,
                        "provenance": {"vendor": "unknown", "as_of": "2026-09-11T21:09:25+00:00"},
                    }
                ],
                "rankings": [
                    {
                        "ticker": "AAPL",
                        "rank": 1,
                        "provenance": {"vendor": "unknown", "as_of": "2026-09-11T21:09:25+00:00"},
                    }
                ],
                "provenance": {"vendor_primary": "yahoo", "as_of": "2026-09-11T21:09:25+00:00"},
                "top5_tickers": ["AAPL"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rj, "LAST_SCAN_PATH", path)
    scan = rj.latest_scan()
    assert scan["recommendations"][0]["provenance"]["vendor"] == "yahoo"
    assert scan["provenance"].get("vendor") == "yahoo"


def test_get_fund_score_from_fresh_scan(tmp_path, monkeypatch):
    _write_scan(tmp_path, monkeypatch, hours_ago=1, score=77)
    info = rj.get_fund_score("AAPL")
    assert info["stale"] is False
    assert info["source"] == "last_scan"
    assert info["score"] == 77
    assert info["in_top5"] is True
    assert info["score_field"] == "risk_adjusted_score"


def test_get_fund_score_shares_ranker_quality_columns(tmp_path, monkeypatch):
    path = tmp_path / "last_scan.json"
    ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    path.write_text(
        json.dumps(
            {
                "ts": ts,
                "top5_tickers": ["AAPL"],
                "rankings": [
                    {
                        "ticker": "AAPL",
                        "rank": 1,
                        "risk_adjusted_score": 81,
                        "quality_score": 74,
                        "quality_roe_score": 80,
                        "quality_margin_score": 70,
                        "quality_leverage_score": 65,
                    }
                ],
                "recommendations": [{"ticker": "AAPL", "risk_adjusted_score": 81}],
                "scores_by_ticker": {
                    "AAPL": {
                        "risk_adjusted_score": 81,
                        "growth_score": 77,
                        "quality_score": 74,
                        "quality_roe_score": 80,
                        "quality_margin_score": 70,
                        "quality_leverage_score": 65,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rj, "LAST_SCAN_PATH", path)
    info = rj.get_fund_score("AAPL")
    assert info["score"] == 81
    assert info["score_field"] == "risk_adjusted_score"
    assert info["quality_score"] == 74
    assert info["quality_roe_score"] == 80
    assert info["quality_leverage_score"] == 65


def test_build_scores_index_keeps_quality_split():
    rankings = [
        {
            "ticker": "MSFT",
            "growth_score": 70,
            "model_score": 71,
            "risk_adjusted_score": 69,
            "quality_score": 60,
            "quality_roe_score": 55,
        }
    ]
    index = rj._build_scores_index(rankings, [])
    assert index["MSFT"]["risk_adjusted_score"] == 69
    assert index["MSFT"]["quality_score"] == 60
    assert index["MSFT"]["quality_roe_score"] == 55


def test_get_fund_score_passes_llm_signal_from_recommendations(tmp_path, monkeypatch):
    path = tmp_path / "last_scan.json"
    ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    path.write_text(
        json.dumps(
            {
                "ts": ts,
                "top5_tickers": ["AAPL"],
                "rankings": [{"ticker": "AAPL", "rank": 1, "risk_adjusted_score": 80}],
                "recommendations": [
                    {
                        "ticker": "AAPL",
                        "risk_adjusted_score": 80,
                        "llm_key_signal": "bearish",
                        "sentiment_label": "negative",
                        "sentiment_score": 32,
                    }
                ],
                "scores_by_ticker": {"AAPL": {"risk_adjusted_score": 80, "growth_score": 70}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rj, "LAST_SCAN_PATH", path)
    info = rj.get_fund_score("AAPL")
    assert info["llm_key_signal"] == "bearish"
    assert info["sentiment_label"] == "negative"
    assert info["score"] == 80


def test_get_fund_score_stale_keeps_real_score(tmp_path, monkeypatch):
    """Past freshness window → as-of stale flag, but last real score is kept (not 50)."""
    _write_scan(tmp_path, monkeypatch, hours_ago=80, score=80)
    info = rj.get_fund_score("AAPL")
    assert info["stale"] is True
    assert info["score"] == 80.0
    assert info["source"] == "last_scan_stale"


def test_get_fund_score_weekend_age_still_fresh_under_72h(tmp_path, monkeypatch):
    """Fri→Mon (~41h) must NOT force score=50 under the 72h session window."""
    _write_scan(tmp_path, monkeypatch, hours_ago=41, score=73)
    info = rj.get_fund_score("AAPL")
    assert info["stale"] is False
    assert info["score"] == 73.0
    assert info["source"] == "last_scan"


def test_signal_max_age_tighter_than_scan_window(tmp_path, monkeypatch):
    """SIGNAL_MAX_AGE_HOURS=24 makes a 30h book stale while Scan default is 72h."""
    monkeypatch.setenv("SCAN_STALE_AFTER_HOURS", "72")
    monkeypatch.setenv("SIGNAL_MAX_AGE_HOURS", "24")
    _write_scan(tmp_path, monkeypatch, hours_ago=30, score=80)
    assert rj.signal_max_age_hours() == 24.0
    assert rj.freshness_limit_hours() == 24.0
    assert rj.scan_is_stale() is True
    info = rj.get_fund_score("AAPL")
    assert info["stale"] is True
    assert info["score"] == 80.0
    scan = rj.latest_scan()
    assert scan["stale"] is True
    assert scan["signal_max_age_hours"] == 24.0


def test_get_fund_score_missing_ticker(tmp_path, monkeypatch):
    _write_scan(tmp_path, monkeypatch, hours_ago=1)
    info = rj.get_fund_score("ZZZZ")
    assert info["score"] == 50.0
    assert info["stale"] is False
    assert info["source"] == "missing"


def test_get_fund_score_no_book_defaults_to_50(tmp_path, monkeypatch):
    path = tmp_path / "last_scan.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(rj, "LAST_SCAN_PATH", path)
    info = rj.get_fund_score("AAPL")
    assert info["score"] == 50.0
    assert info["stale"] is True
    assert info["source"] == "default"


def test_sanitize_drops_dataframe_like_and_private():
    class FakeDF:
        def __len__(self):
            return 3
        @property
        def columns(self):
            return ["Open", "Close"]

    # without pandas DataFrame isinstance, still strips private keys
    cleaned = rj.sanitize({"ticker": "AAPL", "_debug_all_data": {"x": 1}, "score": 70.0})
    assert cleaned["ticker"] == "AAPL"
    assert cleaned["score"] == 70.0
    assert "_debug_all_data" not in cleaned


def test_deep_trade_plan_overlay(tmp_path, monkeypatch):
    path = tmp_path / "last_deep.json"
    path.write_text(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "reports": {
                    "NVDA": {
                        "ticker": "NVDA",
                        "trade_plan": {
                            "stance": "constructive",
                            "action": "buy_zone",
                            "stop_loss": 100.5,
                            "targets": [120.0, 135.0],
                            "entry_zone": {"low": 108, "high": 112},
                        },
                        "short_term": {"score": 72},
                        "long_term": {"score": 68},
                        "advisory": {
                            "roles": [{"role": "bull", "places_order": False}],
                            "top_n_after_suggestion": ["NVDA", "AAPL"],
                            "places_order": False,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rj, "LAST_DEEP_PATH", path)
    plan = rj.get_deep_trade_plan("NVDA")
    assert plan is not None
    assert plan["stop_loss"] == 100.5
    assert plan["targets"][0] == 120.0
    assert "advisory" not in plan
    assert "top_n_after_suggestion" not in plan
    assert "memo" not in plan


def test_map_engine_progress_fetch_vs_llm():
    fetch = rj.map_engine_progress(37, 74, 74)
    assert fetch["phase"] == "fetch"
    assert 20 <= fetch["pct"] <= 55
    assert fetch["total"] == 74
    llm = rj.map_engine_progress(8, 15, 74)
    assert llm["phase"] == "llm"
    assert llm["pct"] >= 58
    assert llm["total"] == 74  # never shrink the universe on the bar


def test_slim_recommendation_bilingual_names():
    slim = rj._slim_recommendation(
        {
            "ticker": "TSM",
            "name": "台積電",
            "growth_score": 90,
            "risk_adjusted_score": 80.12,
            "sentiment": {"composite_score": 70},
            "news": [{"title": "Hello", "sentiment": "positive", "publisher": "X"}],
        }
    )
    assert slim["name"] == "Taiwan Semiconductor"
    assert "台積" in (slim["name_zh"] or "")
    assert slim["news"][0]["title"] == "Hello"


def test_slim_ranking_includes_quality_split():
    slim = rj._slim_ranking(
        {
            "ticker": "AAPL",
            "rank": 1,
            "risk_adjusted_score": 80,
            "quality_score": 71.2,
            "quality_roe_score": 88,
            "quality_margin_score": 60,
            "quality_leverage_score": 70,
        }
    )
    assert slim["quality_score"] == 71.2
    assert slim["quality_roe_score"] == 88.0


def test_write_json_atomic(tmp_path):
    path = tmp_path / "last_scan.json"
    rj._write_json(path, {"ok": True, "rankings": [{"ticker": "AAPL"}]})
    assert json.loads(path.read_text(encoding="utf-8"))["ok"] is True
    assert not (tmp_path / "last_scan.json.tmp").exists()


def test_resolve_job_uses_disk_cache(tmp_path, monkeypatch):
    _write_scan(tmp_path, monkeypatch, hours_ago=1, score=77)
    payload = json.loads((tmp_path / "last_scan.json").read_text(encoding="utf-8"))
    payload["job_id"] = "job-1"
    payload["available"] = True
    (tmp_path / "last_scan.json").write_text(json.dumps(payload), encoding="utf-8")
    job = rj.resolve_job("scan", "job-1")
    assert job["status"] == "done"
    assert job["result"]["recommendations"][0]["ticker"] == "AAPL"


def test_slim_deep_report_attaches_memo_not_an_order():
    slim = rj._slim_deep_report(
        {
            "ticker": "AAPL",
            "trade_plan": {
                "stance": "constructive",
                "action": "watch",
                "entry_zone": {"low": 1, "high": 2},
                "stop_loss": 0.9,
                "targets": [3],
            },
            "strategy": {"rationale": "Stay patient."},
            "avoid": {"reasons": ["Extended"]},
            "quant_score": 66,
            "technical": {"price": 1.5, "price_source": "yahoo_regular_market"},
        }
    )
    assert slim["memo"]["places_order"] is False
    assert slim["memo"]["thesis"]
    assert slim["memo"]["risks"]
    assert slim["memo"]["levels"]["stop_loss"] == 0.9
    assert slim["advisory"]["places_order"] is False
    assert slim["advisory"]["roles"]


