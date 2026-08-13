import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents import marketdata_options


def _payload_row(symbol="TSLA260821C00300000", updated=None, side="call", strike=300):
    now = int(datetime.now(timezone.utc).timestamp())
    return {
        "optionSymbol": symbol,
        "underlying": "TSLA",
        "expiration": 1787342400,
        "side": side,
        "strike": strike,
        "dte": 23,
        "updated": updated if updated is not None else now,
        "bid": 10.0,
        "bidSize": 5,
        "mid": 10.25,
        "ask": 10.5,
        "askSize": 5,
        "last": 10.2,
        "openInterest": 500,
        "volume": 100,
        "inTheMoney": True,
        "intrinsicValue": 8.0,
        "extrinsicValue": 2.25,
        "underlyingPrice": 308.0,
        "iv": 0.45,
        "delta": 0.55,
        "gamma": 0.02,
        "theta": -0.1,
        "vega": 0.2,
    }


def _columnar(payload):
    fields = marketdata_options._COLUMN_FIELDS
    result = {"s": "ok"}
    for field in fields:
        if field in payload:
            result[field] = [payload[field]]
    return result


def test_marketdata_realtime_chain_is_actionable(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    responses = iter([
        {"s": "ok", "expirations": ["2026-08-21"]},
        _columnar(_payload_row()),
    ])
    monkeypatch.setattr(marketdata_options, "_get", lambda *args: next(responses))

    result = marketdata_options.fetch_marketdata_options_chain("TSLA", 300)

    assert result["source"] == "marketdata_options"
    assert result["actionable"] is True
    assert result["delayed"] is False
    assert result["selected_expiry"] == "2026-08-21"
    assert result["calls"][0]["delta"] == 0.55
    assert result["calls"][0]["quote_time"].endswith("+00:00")
    assert result["calls"][0]["market_valid"] is True


def test_marketdata_stale_quote_is_research_only(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    stale = int(datetime.now(timezone.utc).timestamp()) - 3600
    responses = iter([
        {"s": "ok", "expirations": ["2026-08-21"]},
        _columnar(_payload_row(updated=stale)),
    ])
    monkeypatch.setattr(marketdata_options, "_get", lambda *args: next(responses))

    result = marketdata_options.fetch_marketdata_options_chain("TSLA", 300)

    assert result["actionable"] is False
    assert result["delayed"] is True
    assert result["calls"][0]["actionable"] is False


def test_marketdata_no_data_is_provider_incomplete(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    responses = iter([
        {"s": "ok", "expirations": ["2026-08-21"]},
        {"s": "no_data", "nextTime": 1787342400},
    ])
    monkeypatch.setattr(marketdata_options, "_get", lambda *args: next(responses))

    result = marketdata_options.fetch_marketdata_options_chain("TSLA", 300)

    assert result["error"] == "provider_unavailable"
    assert result["error_code"] == "provider_incomplete"


def test_marketdata_empty_expirations_is_provider_incomplete(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    monkeypatch.setattr(marketdata_options, "_get", lambda *args: {"s": "ok", "expirations": []})

    result = marketdata_options.fetch_marketdata_options_chain("TSLA", 300)

    assert result["error_code"] == "provider_incomplete"
    assert result["provider_reason"] == "empty_expirations"


def test_marketdata_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "")

    result = marketdata_options.fetch_marketdata_options_chain("TSLA", 300)

    assert result["error_code"] == "not_configured"


def test_marketdata_contract_quote_normalizes_and_verifies(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    monkeypatch.setattr(marketdata_options, "_get", lambda *args: _columnar(_payload_row()))

    result = marketdata_options.fetch_marketdata_option_contract("TSLA", "TSLA260821C00300000")

    assert result["contract_symbol"] == "TSLA260821C00300000"
    assert result["option_type"] == "call"
    assert result["actionable"] is True
    assert result["implied_volatility"] == 0.45


def test_marketdata_contract_mismatch_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    monkeypatch.setattr(marketdata_options, "_get", lambda *args: _columnar(_payload_row()))

    result = marketdata_options.fetch_marketdata_option_contract("TSLA", "TSLA260821C00300001")

    assert result["error_code"] == "provider_incomplete"
    assert result["provider_reason"] == "contract_mismatch"


def test_marketdata_underlying_mismatch_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    monkeypatch.setattr(marketdata_options, "_get", lambda *args: _columnar(_payload_row()))

    result = marketdata_options.fetch_marketdata_option_contract("AAPL", "TSLA260821C00300000")

    assert result["error_code"] == "provider_incomplete"
    assert result["provider_reason"] == "contract_mismatch"


def test_marketdata_http_203_is_accepted_as_success(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    monkeypatch.setattr(marketdata_options, "requests", _FakeRequests(203, {"s": "ok", "expirations": ["2026-08-21"]}))

    result = marketdata_options._get("/options/expirations/TSLA/", {}, None)

    assert result.get("expirations") == ["2026-08-21"]


def test_marketdata_http_429_is_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    monkeypatch.setattr(marketdata_options, "requests", _FakeRequests(429, {}))

    result = marketdata_options._get("/options/expirations/TSLA/", {}, None)

    assert result["error_code"] == "rate_limit"
    assert result["error"] == "rate_limited"


def test_marketdata_http_403_is_authentication(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    monkeypatch.setattr(marketdata_options, "requests", _FakeRequests(403, {}))

    result = marketdata_options._get("/options/expirations/TSLA/", {}, None)

    assert result["error_code"] == "authentication"


def test_marketdata_strike_interval_filter_built_for_spot(monkeypatch) -> None:
    monkeypatch.setattr(marketdata_options, "MARKETDATA_API_TOKEN", "token")
    calls = {
        "s": "ok",
        "expirations": ["2026-08-21"],
    }
    chain = {
        "s": "ok",
        "optionSymbol": ["TSLA260821C00300000"],
        "underlying": ["TSLA"],
        "expiration": [1787342400],
        "side": ["call"],
        "strike": [300],
        "updated": [int(datetime.now(timezone.utc).timestamp())],
        "bid": [10.0],
        "ask": [10.5],
        "mid": [10.25],
        "last": [10.2],
        "openInterest": [500],
        "volume": [100],
        "underlyingPrice": [308.0],
        "iv": [0.45],
        "delta": [0.55],
        "gamma": [0.02],
        "theta": [-0.1],
        "vega": [0.2],
    }
    seen = {}

    def fake_get(path, params, deadline):
        if "expirations" in path:
            return calls
        seen.update(params)
        return chain

    monkeypatch.setattr(marketdata_options, "_get", fake_get)

    marketdata_options.fetch_marketdata_options_chain("TSLA", 300)

    assert seen.get("strike") == "210.0-390.0"
    assert seen.get("expiration") == "2026-08-21"


class _FakeResponse:
    def __init__(self, status_code, json_body):
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        return self._json_body


class _FakeRequests:
    def __init__(self, status_code, json_body):
        self._response = _FakeResponse(status_code, json_body)

    def get(self, *args, **kwargs):
        return self._response
