from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents import options_quote_adapter
from agents.options_quote_adapter import fetch_option_quote
from agents.options_risk_agent import evaluate_option_risk


def _chain(*, bid=3.4, ask=3.6, last_trade=None, volume=50, open_interest=200):
    timestamp = last_trade or datetime.now(timezone.utc)
    calls = pd.DataFrame([{
        "contractSymbol": "LLY260918C01000000",
        "bid": bid,
        "ask": ask,
        "lastPrice": 3.5,
        "lastTradeDate": timestamp,
        "volume": volume,
        "openInterest": open_interest,
        "impliedVolatility": 0.35,
    }])
    return type("Chain", (), {"calls": calls, "puts": pd.DataFrame()})()


def _rule():
    return {
        "monitor_symbol": "LLY260918C01000000",
        "expiry": "2026-09-18",
        "instrument_type": "option",
    }


def _market_time() -> datetime:
    return datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)


def test_entry_uses_ask_and_exit_uses_bid(monkeypatch) -> None:
    now = _market_time()
    monkeypatch.setattr(
        options_quote_adapter.yf,
        "Ticker",
        lambda ticker: type("Ticker", (), {"info": {"marketState": "REGULAR"}, "option_chain": lambda self, expiry: _chain(last_trade=now)})(),
    )

    entry = fetch_option_quote("LLY", _rule(), "option_entry", now=now)
    target = fetch_option_quote("LLY", _rule(), "option_target_1", now=now)

    assert entry["price"] == 3.6
    assert target["price"] == 3.4
    assert entry["agent_trace"][-1] == {"stage": "executable_price", "status": "done", "side": "ask"}


def test_stale_last_trade_is_rejected(monkeypatch) -> None:
    now = _market_time()
    stale = now - timedelta(minutes=30)
    monkeypatch.setattr(
        options_quote_adapter.yf,
        "Ticker",
        lambda ticker: type("Ticker", (), {"info": {"marketState": "REGULAR"}, "option_chain": lambda self, expiry: _chain(last_trade=stale)})(),
    )

    quote = fetch_option_quote("LLY", _rule(), "option_entry", now=now)

    assert quote["available"] is False
    assert quote["stale_reason"] == "stale_option_trade"


def test_zero_bid_blocks_entry_and_exit_with_specific_reasons(monkeypatch) -> None:
    now = _market_time()
    monkeypatch.setattr(
        options_quote_adapter.yf,
        "Ticker",
        lambda ticker: type("Ticker", (), {"info": {"marketState": "REGULAR"}, "option_chain": lambda self, expiry: _chain(bid=0, ask=0.1, last_trade=now)})(),
    )

    entry_quote = fetch_option_quote("LLY", _rule(), "option_entry", now=now)
    exit_quote = fetch_option_quote("LLY", _rule(), "option_stop", now=now)

    assert entry_quote["available"] is False
    assert entry_quote["stale_reason"] == "non_executable_market"
    assert exit_quote["available"] is False
    assert exit_quote["stale_reason"] == "zero_bid_exit"


def test_risk_judge_cannot_approve_failed_hard_gate() -> None:
    result = evaluate_option_risk({
        "available": True,
        "stale": False,
        "spread_pct": 30,
        "contract_symbol": "A",
        "volume": 100,
        "open_interest": 100,
    }, {"monitor_symbol": "A"})

    assert result["status"] == "rejected"
    assert result["hard_gate_passed"] is False
    assert "spread_acceptable" in result["violations"]


def test_option_market_closed_fails_before_fetch(monkeypatch) -> None:
    called = False

    def ticker(symbol):
        nonlocal called
        called = True

    monkeypatch.setattr(options_quote_adapter.yf, "Ticker", ticker)

    quote = fetch_option_quote(
        "LLY", _rule(), "option_entry",
        now=datetime(2026, 7, 29, 22, 0, tzinfo=timezone.utc),
    )

    assert quote["stale_reason"] == "option_market_closed"
    assert called is False


def test_yahoo_non_regular_state_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        options_quote_adapter.yf,
        "Ticker",
        lambda ticker: type("Ticker", (), {
            "info": {"marketState": "CLOSED"},
            "option_chain": lambda self, expiry: (_ for _ in ()).throw(AssertionError("chain fetched")),
        })(),
    )

    quote = fetch_option_quote("LLY", _rule(), "option_entry", now=_market_time())

    assert quote["stale_reason"] == "option_market_not_regular"
