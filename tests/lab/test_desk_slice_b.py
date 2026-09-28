"""Wave 3 Slice B — Desk tear, advisory memo, digest. Never places orders."""
from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backend.api.desk_digest import build_desk_digest, classify_orders
from backend.lab import advisory_debate as ad
from backend.lab import quantstats_tear as qs


@pytest.fixture()
def lab_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_INSTRUCTOR", "0")
    monkeypatch.setenv("LAB_FINBERT", "0")
    monkeypatch.setenv("LAB_QUANTSTATS", "0")
    monkeypatch.setenv("LAB_ADVISORY", "0")
    monkeypatch.setattr("backend.lab.advisory_debate.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("backend.lab.quantstats_tear.DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ad, "_METRICS_PATH", tmp_path / "lab_advisory_metrics.json")
    monkeypatch.setattr(ad, "_BUDGET_PATH", tmp_path / "lab_advisory_budget.json")
    return tmp_path


_ET = ZoneInfo("America/New_York")


def test_advisory_module_never_mentions_submit_order():
    src = inspect.getsource(ad)
    assert "submit_order" not in src
    assert "Never places orders" in (ad.__doc__ or "")


def test_worker_module_does_not_import_advisory():
    from backend.trading.engine import worker as worker_mod

    src = inspect.getsource(worker_mod)
    assert "advisory_debate" not in src
    assert "run_advisory_debate" not in src


def test_desk_advisory_without_lab_flag(lab_data_dir, monkeypatch):
    monkeypatch.setenv("LAB_ADVISORY", "0")
    out = ad.run_advisory_debate("AAPL", fund_score=80, product=True)
    assert out["enabled"] is True
    assert out["places_order"] is False
    assert out["llm_used"] is False
    assert len(out["roles"]) == 3
    assert {r["role"] for r in out["roles"]} == {"bull", "bear", "risk"}
    for role in out["roles"]:
        assert role["places_order"] is False


def test_llm_budget_zero_skips_llm(lab_data_dir, monkeypatch):
    monkeypatch.setenv("LAB_ADVISORY", "1")
    monkeypatch.setenv("LAB_ADVISORY_MAX_LLM_CALLS", "0")

    def boom(**kwargs):
        raise AssertionError("LLM must not run when budget is 0")

    monkeypatch.setattr(ad, "_call_llm_committee", boom)
    out = ad.run_advisory_debate("MSFT", product=True, use_llm=True)
    assert out["llm_used"] is False
    assert out["places_order"] is False
    assert out["roles"]


def test_llm_cannot_set_places_order(lab_data_dir, monkeypatch):
    monkeypatch.setenv("LAB_ADVISORY", "1")
    monkeypatch.setenv("LAB_ADVISORY_MAX_LLM_CALLS", "5")

    def fake(ticker, **kwargs):
        return ad._coerce_roles(
            ticker,
            [
                {
                    "role": "bull",
                    "thesis": "market buy now",
                    "confidence": 0.99,
                    "key_points": ["submit"],
                    "places_order": True,
                },
                {"role": "bear", "thesis": "risk", "confidence": 0.4, "key_points": []},
                {"role": "risk", "thesis": "halt", "confidence": 0.9, "key_points": []},
            ],
        )

    monkeypatch.setattr(ad, "_call_llm_committee", fake)
    out = ad.run_advisory_debate("NVDA", product=True, use_llm=True)
    assert out["llm_used"] is True
    assert out["places_order"] is False
    for role in out["roles"]:
        assert role["places_order"] is False


def test_research_memo_has_four_pillars(lab_data_dir, monkeypatch):
    monkeypatch.setenv("LAB_ADVISORY", "0")
    report = {
        "ticker": "AAPL",
        "quant_score": 72,
        "risk_adjusted_score": 70,
        "trade_plan": {
            "stance": "constructive",
            "action": "buy_zone",
            "entry_zone": {"low": 180, "high": 185},
            "stop_loss": 170,
            "targets": [200, 210],
            "confirmation_price": 186,
        },
        "strategy": {"rationale": "Quality compounder with pullback."},
        "avoid": {"reasons": ["Gap risk into earnings"]},
        "provenance": {"vendor": "yahoo", "as_of": "2026-09-15T20:00:00+00:00"},
        "sec_evidence": {"form": "10-K", "summary": "Cash flow remains strong."},
        "news": [{"title": "Supplier win", "sentiment": "positive"}],
    }
    attached = ad.attach_desk_advisory(report)
    memo = attached["memo"]
    assert memo["places_order"] is False
    assert memo["thesis"]
    assert memo["risks"]
    assert memo["levels"]["stop_loss"] == 170
    assert memo["levels"]["entry_low"] == 180
    assert any("yahoo" in s or "SEC" in s or "Supplier" in s for s in memo["sources"])
    assert attached["advisory"]["places_order"] is False


def test_ticker_tear_stub_without_network(lab_data_dir, monkeypatch):
    monkeypatch.setenv("LAB_QUANTSTATS", "0")
    closes = pd.Series(
        [100.0, 101.0, 102.5, 101.5, 103.0],
        index=pd.date_range("2026-01-02", periods=5, freq="B"),
    )
    spy = pd.Series(
        [0.001, 0.0, -0.002, 0.003],
        index=closes.index[1:],
    )
    monkeypatch.setattr(qs, "load_spy_returns", lambda *a, **k: spy)
    out = qs.build_desk_tear(ticker="AAPL", close=closes)
    assert out["scope"] == "ticker"
    assert out["ticker"] == "AAPL"
    assert out["complete"] is True
    assert out["engine"] == "json_stub"
    assert out["places_order"] is False
    assert out["summary"]["n_days"] >= 2
    assert Path(out["stub_path"]).exists()
    cached = qs.load_latest_tear()
    assert cached["ticker"] == "AAPL"


def test_paper_tear_insufficient_curve(lab_data_dir, monkeypatch):
    monkeypatch.setattr(qs, "load_paper_equity_curve", lambda: ([], ["empty_paper_curve"]))
    monkeypatch.setattr(qs, "load_spy_returns", lambda *a, **k: None)
    out = qs.build_desk_tear()
    assert out["complete"] is False
    assert "insufficient_equity_curve" in out["notes"]
    assert out["places_order"] is False


def test_digest_classifies_filled_vs_pending():
    now = datetime(2026, 9, 15, 17, 0, tzinfo=_ET)
    orders = [
        {
            "symbol": "AAPL",
            "status": "filled",
            "filled_at": "2026-09-15T18:00:00+00:00",
        },
        {"symbol": "MSFT", "status": "new"},
        {"symbol": "NVDA", "status": "accepted"},
        {
            "symbol": "GOOGL",
            "status": "filled",
            "filled_at": "2026-09-01T18:00:00+00:00",
        },
    ]
    book = classify_orders(orders, now_et=now)
    assert book["filled_today"] == ["AAPL"]
    assert "MSFT" in book["pending"]
    assert "NVDA" in book["pending"]
    assert "GOOGL" in book["filled"]
    assert book["pending_count"] == 2


def test_build_desk_digest_offline(monkeypatch):
    now = datetime(2026, 9, 15, 16, 15, tzinfo=_ET)
    monkeypatch.setattr(
        "backend.trading.alpaca_broker.AlpacaBroker",
        lambda: (_ for _ in ()).throw(RuntimeError("skip alpaca")),
    )
    digest = build_desk_digest(
        scan={
            "available": True,
            "stale": False,
            "ts": "2026-09-15T20:00:00+00:00",
            "top5_tickers": ["AAPL", "MSFT"],
        },
        worker={"strategy": "breakout", "running": True, "skip_counts": {"rsi_overbought": 4}},
        orders=[{"symbol": "AAPL", "status": "new"}],
        paper={"benchmark": {"alpha_net_of_costs_pct": -0.4, "beats_spy_net": False}},
        now_et=now,
    )
    assert digest["places_order"] is False
    assert digest["scan"]["top5"][0] == "AAPL"
    assert digest["book"]["pending"] == ["AAPL"]
    assert digest["pnl"]["beats_spy_net"] is False
    assert "超買" in digest["skips"]["summary_zh"]
