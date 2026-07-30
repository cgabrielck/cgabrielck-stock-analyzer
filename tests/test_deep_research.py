import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents import deep_research
from agents.deep_research import (
    _avoid_assessment,
    _build_options_plan,
    _build_trade_plan,
    _long_term_score,
    _short_term_score,
)


@pytest.fixture(autouse=True)
def _disable_live_sec(monkeypatch):
    monkeypatch.setattr(deep_research, "get_latest_filing", lambda *args, **kwargs: {
        "available": False, "status": "not_found", "provenance": {},
    })


def test_deep_scores_reward_aligned_ema_trend() -> None:
    stock = {"growth_score": 80, "risk_penalty": 0, "risk_penalty_level": "low", "metrics_used": 6}
    technical = {
        "technical_score": 75, "price": 120, "ema_9": 115, "ema_21": 110, "ema_50": 100,
        "ema_200": 90, "sma_50": 105, "sma_200": 95, "adx_14": 30,
    }

    assert _short_term_score(stock, technical) >= 75
    assert _long_term_score(stock, technical) >= 80


def test_avoid_requires_explainable_reasons() -> None:
    stock = {"risk_penalty_level": "high", "metrics_used": 6}
    technical = {"price": 80, "sma_50": 90, "sma_200": 100}

    result = _avoid_assessment(stock, technical, 40, 45)

    assert result["active"] is True
    assert len(result["reasons"]) >= 2


def test_analyze_ticker_fetches_only_requested_stock(monkeypatch) -> None:
    fetched = []
    monkeypatch.setattr(deep_research, "fetch_stock_data", lambda ticker, force_refresh=False: fetched.append(ticker) or {
        "ticker": ticker, "revenue_growth": 20, "eps_growth": 20, "profit_margin": 20,
        "peg": 1, "roe": 20, "debt_equity": 0.5,
    })
    monkeypatch.setattr(deep_research, "compute_technical_indicators", lambda *args, **kwargs: {
        "technical_score": 70, "price": 100, "risk_metrics": {"available": False},
        "ema_9": 101, "ema_21": 100, "ema_50": 95, "ema_200": 80, "sma_50": 96,
        "sma_200": 82, "adx_14": 25,
    })
    monkeypatch.setattr(deep_research, "fetch_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(deep_research, "fetch_options_chain", lambda *args, **kwargs: {"error": "none"})
    monkeypatch.setattr(deep_research, "fetch_trading_session_ranges", lambda *args, **kwargs: {"sessions": {}})
    monkeypatch.setattr(deep_research, "suggest_trading_strategy", lambda *args, **kwargs: {"reasoning": "ok"})

    result = deep_research.analyze_ticker("AAPL")

    assert fetched == ["AAPL"]
    assert result["ticker"] == "AAPL"
    assert result["quant_score"] is not None


def test_fundamental_and_technical_sources_run_in_parallel(monkeypatch) -> None:
    def fundamentals(ticker, **kwargs):
        time.sleep(0.08)
        return {
            "ticker": ticker, "revenue_growth": 20, "eps_growth": 20, "profit_margin": 20,
            "peg": 1, "roe": 20, "debt_equity": 0.5,
        }

    def technical(*args, **kwargs):
        time.sleep(0.08)
        return {
            "technical_score": 70, "price": 100, "risk_metrics": {"available": False},
            "ema_9": 101, "ema_21": 100, "ema_50": 95, "sma_50": 96, "adx_14": 25,
        }

    monkeypatch.setattr(deep_research, "fetch_stock_data", fundamentals)
    monkeypatch.setattr(deep_research, "compute_technical_indicators", technical)
    monkeypatch.setattr(deep_research, "fetch_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(deep_research, "fetch_options_chain", lambda *args, **kwargs: {"error": "none"})
    monkeypatch.setattr(deep_research, "fetch_trading_session_ranges", lambda *args, **kwargs: {"sessions": {}})
    monkeypatch.setattr(deep_research, "suggest_trading_strategy", lambda *args, **kwargs: {"reasoning": "ok"})
    started = time.monotonic()

    result = deep_research.analyze_ticker("TEST")

    assert time.monotonic() - started < 0.14
    assert result["timing"]["stages"]["fundamental"]["duration_ms"] >= 70
    assert result["timing"]["stages"]["technical"]["duration_ms"] >= 70


def test_bullish_trade_plan_has_ordered_levels() -> None:
    technical = {"price": 100, "atr_14": 4, "ema_21": 97, "sma_50": 94, "bb_lower": 92, "bb_upper": 106}

    plan = _build_trade_plan({}, technical, 75, 70, {"active": False})

    assert plan["stance"] == "bullish"
    assert plan["position_side"] == "long"
    assert plan["stop_type"] == "sell_stop"
    assert plan["stop_loss"] < plan["entry_zone"]["low"] < plan["entry_zone"]["high"]
    assert plan["targets"][0] > plan["entry_zone"]["high"]
    assert plan["targets"][0] == round(plan["entry_reference"] + 1.5 * plan["risk_per_share"], 2)


def test_bearish_trade_plan_has_ordered_levels() -> None:
    technical = {"price": 100, "atr_14": 4, "ema_21": 103, "sma_50": 106, "bb_lower": 94, "bb_upper": 108}

    plan = _build_trade_plan({}, technical, 35, 40, {"active": True})

    assert plan["stance"] == "bearish"
    assert plan["position_side"] == "short_or_hedge"
    assert plan["stop_type"] == "buy_to_cover_stop"
    assert plan["stop_loss"] > plan["entry_zone"]["high"]
    assert plan["targets"][0] < plan["entry_zone"]["low"]
    assert plan["targets"][0] == round(plan["entry_reference"] - 1.5 * plan["risk_per_share"], 2)


def test_bearish_plan_never_presents_cover_stop_as_long_stop_for_reported_tickers() -> None:
    for ticker, price, atr in (("TSLA", 320, 14), ("MU", 130, 6), ("ASTS", 45, 4)):
        technical = {
            "ticker": ticker, "price": price, "atr_14": atr,
            "ema_21": price * 1.03, "sma_50": price * 1.08,
            "bb_lower": price * 0.9, "bb_upper": price * 1.12,
        }

        plan = _build_trade_plan({}, technical, 35, 40, {"active": True})

        assert plan["stance"] == "bearish"
        assert plan["stop_type"] == "buy_to_cover_stop"
        assert plan["targets"][1] <= plan["targets"][0] < plan["entry_zone"]["low"]
        assert plan["entry_zone"]["low"] < plan["entry_zone"]["high"] < plan["stop_loss"]


def test_options_plan_selects_liquid_contract_and_sets_premium_exits() -> None:
    options = {
        "selected_expiry": "2026-08-21",
        "calls": [
            {"strike": 100, "bid": 2.0, "ask": 4.0, "mid": 3.0, "open_interest": 10, "volume": 0, "spread_pct": 66.7},
            {"strike": 105, "bid": 2.4, "ask": 2.6, "mid": 2.5, "open_interest": 500, "volume": 100, "spread_pct": 8.0},
        ],
        "puts": [],
    }

    plan = _build_options_plan(options, {"stance": "bullish", "stop_loss": 95})

    assert plan["action"] == "buy_to_open"
    assert plan["contract"]["strike"] == 105
    assert plan["max_entry_premium"] == 2.6
    assert plan["stop_premium"] < plan["max_entry_premium"] < plan["take_profit_premiums"][0]


def test_options_plan_marks_delayed_fallback_source() -> None:
    options = {
        "selected_expiry": "2026-08-21",
        "source": "cboe_delayed_options",
        "delayed": True,
        "calls": [{
            "contract_symbol": "TSLA260821C00300000", "strike": 300,
            "bid": 10.0, "ask": 10.5, "mid": 10.25,
            "open_interest": 500, "volume": 100, "spread_pct": 4.9,
            "source": "cboe_delayed_options", "delayed": True,
            "last_trade_time": "2026-07-30T09:45:00",
        }],
        "puts": [],
    }

    plan = _build_options_plan(options, {"stance": "bullish", "stop_loss": 290})

    assert plan["action"] == "research_only"
    assert plan["data_source"] == "cboe_delayed_options"
    assert plan["delayed_data"] is True
    assert "delayed_option_data" in plan["agent_trace"][0]["warnings"]
    assert plan["agent_trace"][-1]["hard_gate_passed"] is False
    assert "delayed_data_not_actionable" in plan["agent_trace"][-1]["violations"]


def test_options_plan_preserves_provider_error_classification() -> None:
    plan = _build_options_plan({
        "error": "rate_limited", "error_code": "rate_limit", "retry_after_seconds": 300,
    }, {"stance": "bullish"})

    assert plan == {
        "action": "none",
        "reason": "rate_limited",
        "error_code": "rate_limit",
        "retry_after_seconds": 300,
    }


def test_options_plan_preserves_incomplete_provider_response() -> None:
    plan = _build_options_plan({
        "error": "provider_incomplete", "error_code": "provider_incomplete",
        "provider_reason": "empty_expirations", "retry_after_seconds": 300,
    }, {"stance": "bullish"})

    assert plan["action"] == "none"
    assert plan["error_code"] == "provider_incomplete"
    assert plan["retry_after_seconds"] == 300


def test_analyze_tickers_keeps_partial_failures(monkeypatch) -> None:
    def analyze(ticker, **kwargs):
        if ticker == "BAD":
            raise RuntimeError("failed")
        return {"ticker": ticker}

    monkeypatch.setattr(deep_research, "analyze_ticker", analyze)

    results = deep_research.analyze_tickers(["AAPL", "BAD", "MSFT"])

    assert results["AAPL"] == {"ticker": "AAPL"}
    assert results["BAD"]["stage"] == "unexpected"
    assert results["MSFT"] == {"ticker": "MSFT"}


def test_analyze_tickers_runs_in_parallel_and_preserves_order(monkeypatch) -> None:
    def analyze(ticker, **kwargs):
        time.sleep(0.1)
        return {"ticker": ticker}

    monkeypatch.setattr(deep_research, "analyze_ticker", analyze)
    started = time.monotonic()

    results = deep_research.analyze_tickers(["AAPL", "MSFT", "NVDA"])

    assert time.monotonic() - started < 0.25
    assert list(results) == ["AAPL", "MSFT", "NVDA"]


def test_analyze_ticker_emits_stage_progress(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(deep_research, "fetch_stock_data", lambda *args, **kwargs: {
        "ticker": "AAPL", "revenue_growth": 20, "eps_growth": 20, "profit_margin": 20,
        "peg": 1, "roe": 20, "debt_equity": 0.5,
    })
    monkeypatch.setattr(deep_research, "compute_technical_indicators", lambda *args, **kwargs: {
        "technical_score": 70, "price": 100, "risk_metrics": {"available": False},
        "ema_9": 101, "ema_21": 100, "ema_50": 95, "sma_50": 96, "adx_14": 25,
    })
    monkeypatch.setattr(deep_research, "fetch_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(deep_research, "fetch_options_chain", lambda *args, **kwargs: {"error": "none"})
    monkeypatch.setattr(deep_research, "fetch_trading_session_ranges", lambda *args, **kwargs: {"sessions": {}})
    monkeypatch.setattr(deep_research, "suggest_trading_strategy", lambda *args, **kwargs: {"reasoning": "ok"})

    deep_research.analyze_ticker("AAPL", progress_callback=events.append)

    completed = {event["stage"] for event in events if event["state"] == "completed"}
    assert completed == {"fundamental", "technical", "market_data", "strategy"}


def test_batch_progress_completes_ticker_after_stage_events(monkeypatch) -> None:
    def analyze(ticker, progress_callback=None, **kwargs):
        progress_callback({"ticker": ticker, "stage": "strategy", "state": "completed"})
        return {"ticker": ticker}

    events = []
    monkeypatch.setattr(deep_research, "analyze_ticker", analyze)

    deep_research.analyze_tickers(["AAPL"], progress_callback=events.append)

    assert events[-1] == {"ticker": "AAPL", "stage": "ticker", "state": "completed"}


def test_batch_deadline_returns_timeout_without_waiting_for_worker(monkeypatch) -> None:
    def analyze(*args, **kwargs):
        time.sleep(0.2)
        return {"ticker": "AAPL"}

    monkeypatch.setattr(deep_research, "analyze_ticker", analyze)
    started = time.monotonic()

    result = deep_research.analyze_tickers(["AAPL"], batch_timeout_seconds=0.03)

    assert time.monotonic() - started < 0.15
    assert result["AAPL"]["stage"] == "timeout"


def test_market_data_timeout_keeps_core_result(monkeypatch) -> None:
    stock = {"ticker": "AAPL", "growth_score": 70, "risk_penalty": 0, "metrics_used": 6}
    technical = {
        "technical_score": 70, "price": 100, "risk_metrics": {"available": False},
        "ema_9": 101, "ema_21": 100, "ema_50": 95, "sma_50": 96, "adx_14": 25,
    }
    monkeypatch.setattr(deep_research, "MARKET_DATA_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(deep_research, "fetch_options_chain", lambda *args, **kwargs: time.sleep(0.1) or {})
    monkeypatch.setattr(deep_research, "fetch_trading_session_ranges", lambda *args, **kwargs: {"sessions": {}})
    monkeypatch.setattr(deep_research, "fetch_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(deep_research, "suggest_trading_strategy", lambda *args, **kwargs: {"reasoning": "ok"})

    result = deep_research.analyze_selected_stock(stock, technical=technical)

    assert result["ticker"] == "AAPL"
    assert result["schema_version"] == deep_research.DEEP_RESEARCH_SCHEMA_VERSION
    assert result["options"]["error"] == "provider_timeout"
    assert result["trade_plan"]["action"] == "buy"


def test_fundamental_timeout_uses_seed_and_keeps_deterministic_plan(monkeypatch) -> None:
    monkeypatch.setattr(deep_research, "CORE_DATA_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(deep_research, "fetch_stock_data", lambda *args, **kwargs: time.sleep(0.1) or {})
    monkeypatch.setattr(deep_research, "get_seed_fallback", lambda ticker, reason: {
        "ticker": ticker, "revenue_growth": 20, "eps_growth": 20, "profit_margin": 20,
        "peg": 1, "roe": 20, "debt_equity": 0.5,
        "data_quality": {"source": "seed_data", "is_fallback": True, "stale": True},
    })
    monkeypatch.setattr(deep_research, "compute_technical_indicators", lambda *args, **kwargs: {
        "technical_score": 70, "price": 100, "risk_metrics": {"available": False},
        "ema_9": 101, "ema_21": 100, "ema_50": 95, "sma_50": 96, "adx_14": 25,
    })
    monkeypatch.setattr(deep_research, "fetch_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(deep_research, "fetch_options_chain", lambda *args, **kwargs: {"error": "none"})
    monkeypatch.setattr(deep_research, "fetch_trading_session_ranges", lambda *args, **kwargs: {"sessions": {}})
    monkeypatch.setattr(deep_research, "suggest_trading_strategy", lambda *args, **kwargs: {"reasoning": "ok"})

    started = time.monotonic()
    result = deep_research.analyze_ticker("AAPL")

    assert time.monotonic() - started < 0.09
    assert result.get("error") is None
    assert result["trade_plan"]["action"] != "no_trade"
    assert result["provenance"]["fundamentals"]["source"] == "seed_data"


def test_technical_timeout_fails_only_that_ticker_without_waiting(monkeypatch) -> None:
    monkeypatch.setattr(deep_research, "CORE_DATA_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(deep_research, "fetch_stock_data", lambda *args, **kwargs: {"ticker": "AAPL"})
    monkeypatch.setattr(
        deep_research, "compute_technical_indicators", lambda *args, **kwargs: time.sleep(0.1) or {},
    )

    started = time.monotonic()
    result = deep_research.analyze_ticker("AAPL")

    assert time.monotonic() - started < 0.09
    assert result["error"] == "technical_timeout"
    assert result["stage"] == "technical"


def test_force_refresh_propagates_to_enrichment_providers(monkeypatch) -> None:
    calls = {}
    stock = {"ticker": "AAPL", "growth_score": 70, "risk_penalty": 0, "metrics_used": 6}
    technical = {"technical_score": 70, "price": 100, "risk_metrics": {"available": False}}
    def options(*args, **kwargs):
        calls["options"] = kwargs.get("force_refresh")
        return {"error": "none"}

    def sessions(*args, **kwargs):
        calls["sessions"] = kwargs.get("force_refresh")
        return {"sessions": {}}

    def news(*args, **kwargs):
        calls["news"] = kwargs.get("force_refresh")
        return []

    monkeypatch.setattr(deep_research, "fetch_options_chain", options)
    monkeypatch.setattr(deep_research, "fetch_trading_session_ranges", sessions)
    monkeypatch.setattr(deep_research, "fetch_news", news)
    monkeypatch.setattr(deep_research, "suggest_trading_strategy", lambda *args, **kwargs: {"reasoning": "ok"})

    deep_research.analyze_selected_stock(stock, technical=technical, force_refresh=True)

    assert calls == {"options": True, "sessions": True, "news": True}
