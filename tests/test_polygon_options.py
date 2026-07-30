import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents import polygon_options


def _snapshot(timeframe="REAL-TIME"):
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    return {
        "details": {
            "ticker": "O:TSLA260821C00300000", "contract_type": "call",
            "expiration_date": "2026-08-21", "strike_price": 300,
        },
        "last_quote": {
            "bid": 10.0, "ask": 10.5, "midpoint": 10.25,
            "last_updated": now_ns, "timeframe": timeframe,
        },
        "last_trade": {"price": 10.2, "sip_timestamp": now_ns, "timeframe": timeframe},
        "day": {"volume": 100}, "open_interest": 500,
        "implied_volatility": 0.45,
        "greeks": {"delta": 0.55, "gamma": 0.02, "theta": -0.1, "vega": 0.2},
        "underlying_asset": {"price": 300},
    }


def test_polygon_realtime_snapshot_is_actionable(monkeypatch) -> None:
    monkeypatch.setattr(polygon_options, "POLYGON_API_KEY", "key")
    responses = iter([
        {"results": [{"expiration_date": "2026-08-21"}]},
        {"results": [_snapshot("REAL-TIME")]},
    ])
    monkeypatch.setattr(polygon_options, "_get", lambda *args: next(responses))

    result = polygon_options.fetch_polygon_options_chain("TSLA", 300)

    assert result["source"] == "polygon_options"
    assert result["actionable"] is True
    assert result["calls"][0]["quote_time"].endswith("+00:00")
    assert result["calls"][0]["delta"] == 0.55


def test_polygon_delayed_snapshot_is_research_only(monkeypatch) -> None:
    monkeypatch.setattr(polygon_options, "POLYGON_API_KEY", "key")
    responses = iter([
        {"results": [{"expiration_date": "2026-08-21"}]},
        {"results": [_snapshot("DELAYED")]},
    ])
    monkeypatch.setattr(polygon_options, "_get", lambda *args: next(responses))

    result = polygon_options.fetch_polygon_options_chain("TSLA", 300)

    assert result["actionable"] is False
    assert result["delayed"] is True


def test_polygon_basic_plan_without_snapshot_returns_provider_error(monkeypatch) -> None:
    monkeypatch.setattr(polygon_options, "POLYGON_API_KEY", "key")
    responses = iter([
        {"results": [{"expiration_date": "2026-08-21"}]},
        {"error": "provider_unavailable", "error_code": "authentication", "provider_reason": "http_403"},
    ])
    monkeypatch.setattr(polygon_options, "_get", lambda *args: next(responses))

    result = polygon_options.fetch_polygon_options_chain("TSLA", 300)

    assert result["error_code"] == "authentication"


def test_polygon_snapshot_follows_pagination(monkeypatch) -> None:
    monkeypatch.setattr(polygon_options, "POLYGON_API_KEY", "key")
    monkeypatch.setattr(polygon_options, "_get", lambda *args: {
        "results": [{"expiration_date": "2026-08-21"}]
    } if "reference" in args[0] else {
        "results": [_snapshot("REAL-TIME")], "next_url": "https://api.polygon.io/next-page",
    })
    put = _snapshot("REAL-TIME")
    put["details"] = {**put["details"], "ticker": "O:TSLA260821P00300000", "contract_type": "put"}
    monkeypatch.setattr(polygon_options, "_get_url", lambda *args: {"results": [put]})

    result = polygon_options.fetch_polygon_options_chain("TSLA", 300)

    assert result["num_calls"] == 1
    assert result["num_puts"] == 1
    assert result["puts"][0]["option_type"] == "put"


def test_polygon_stale_realtime_quote_is_research_only(monkeypatch) -> None:
    monkeypatch.setattr(polygon_options, "POLYGON_API_KEY", "key")
    stale = _snapshot("REAL-TIME")
    stale["last_quote"]["last_updated"] = 1
    responses = iter([
        {"results": [{"expiration_date": "2026-08-21"}]},
        {"results": [stale]},
    ])
    monkeypatch.setattr(polygon_options, "_get", lambda *args: next(responses))

    result = polygon_options.fetch_polygon_options_chain("TSLA", 300)

    assert result["actionable"] is False
    assert result["calls"][0]["actionable"] is False


def test_polygon_rejects_untrusted_pagination_url() -> None:
    assert polygon_options._trusted_page_url("https://api.polygon.io/next") is True
    assert polygon_options._trusted_page_url("https://evil.example/steal") is False
    assert polygon_options._trusted_page_url("http://api.polygon.io/next") is False


def test_polygon_partial_snapshot_is_research_only(monkeypatch) -> None:
    monkeypatch.setattr(polygon_options, "POLYGON_API_KEY", "key")
    responses = iter([
        {"results": [{"expiration_date": "2026-08-21"}]},
        {"results": [_snapshot("REAL-TIME")], "next_url": "https://evil.example/next"},
    ])
    monkeypatch.setattr(polygon_options, "_get", lambda *args: next(responses))

    result = polygon_options.fetch_polygon_options_chain("TSLA", 300)

    assert result["partial"] is True
    assert result["actionable"] is False
    assert result["delayed"] is True
