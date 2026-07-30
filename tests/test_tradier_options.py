import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents import tradier_options


def _quote(symbol="TSLA260821C00300000"):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return {
        "symbol": symbol, "strike": 300, "option_type": "call",
        "expiration_date": "2026-08-21", "bid": 10.0, "ask": 10.5,
        "last": 10.2, "volume": 100, "open_interest": 500,
        "bid_date": now_ms, "ask_date": now_ms,
        "trade_date": now_ms,
        "greeks": {"delta": 0.55, "gamma": 0.02, "theta": -0.1, "vega": 0.2, "rho": 0.05, "mid_iv": 0.45},
    }


def test_tradier_chain_normalizes_single_object_responses(monkeypatch) -> None:
    monkeypatch.setattr(tradier_options, "TRADIER_API_TOKEN", "token")
    monkeypatch.setattr(tradier_options, "TRADIER_BASE_URL", "https://api.tradier.com/v1")
    responses = iter([
        {"expirations": {"date": "2026-08-21"}},
        {"options": {"option": _quote()}},
    ])
    monkeypatch.setattr(tradier_options, "_get", lambda *args: next(responses))

    result = tradier_options.fetch_tradier_options_chain("TSLA", 300)

    assert result["source"] == "tradier_options"
    assert result["actionable"] is True
    assert result["delayed"] is False
    assert result["calls"][0]["delta"] == 0.55
    assert result["calls"][0]["quote_time"].endswith("+00:00")
    assert result["calls"][0]["market_valid"] is True


def test_tradier_sandbox_is_research_only(monkeypatch) -> None:
    monkeypatch.setattr(tradier_options, "TRADIER_API_TOKEN", "token")
    monkeypatch.setattr(tradier_options, "TRADIER_BASE_URL", "https://sandbox.tradier.com/v1")
    responses = iter([
        {"expirations": {"date": ["2026-08-21"]}},
        {"options": {"option": [_quote()]}},
    ])
    monkeypatch.setattr(tradier_options, "_get", lambda *args: next(responses))

    result = tradier_options.fetch_tradier_options_chain("TSLA", 300)

    assert result["actionable"] is False
    assert result["delayed"] is True


def test_tradier_contract_quote_preserves_actionable_status(monkeypatch) -> None:
    monkeypatch.setattr(tradier_options, "TRADIER_API_TOKEN", "token")
    monkeypatch.setattr(tradier_options, "TRADIER_BASE_URL", "https://api.tradier.com/v1")
    monkeypatch.setattr(tradier_options, "_get", lambda *args: {"quotes": {"quote": _quote()}})

    result = tradier_options.fetch_tradier_option_contract("TSLA260821C00300000")

    assert result["actionable"] is True
    assert result["bid"] == 10.0
    assert result["ask"] == 10.5


def test_tradier_stale_quote_is_research_only(monkeypatch) -> None:
    monkeypatch.setattr(tradier_options, "TRADIER_API_TOKEN", "token")
    monkeypatch.setattr(tradier_options, "TRADIER_BASE_URL", "https://api.tradier.com/v1")
    stale = _quote()
    stale["bid_date"] = stale["ask_date"] = 1
    responses = iter([
        {"expirations": {"date": ["2026-08-21"]}},
        {"options": {"option": [stale]}},
    ])
    monkeypatch.setattr(tradier_options, "_get", lambda *args: next(responses))

    result = tradier_options.fetch_tradier_options_chain("TSLA", 300)

    assert result["actionable"] is False
    assert result["calls"][0]["actionable"] is False


def test_tradier_contract_rejects_identity_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(tradier_options, "TRADIER_API_TOKEN", "token")
    monkeypatch.setattr(tradier_options, "_get", lambda *args: {"quotes": {"quote": _quote("WRONG260821C00300000")}})

    result = tradier_options.fetch_tradier_option_contract("TSLA260821C00300000")

    assert result["provider_reason"] == "contract_mismatch"
