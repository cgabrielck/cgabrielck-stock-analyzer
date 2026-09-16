import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from agents import technical_analyzer


def test_technical_cache_is_separated_by_period(monkeypatch) -> None:
    calls = []
    index = pd.date_range("2025-01-01", periods=220, freq="B")
    history = pd.DataFrame({
        "Open": range(100, 320), "High": range(101, 321), "Low": range(99, 319),
        "Close": range(100, 320), "Volume": [1000] * 220,
    }, index=index)

    class FakeTicker:
        info = {"marketState": "CLOSED", "regularMarketPrice": 319}

        def history(self, period, **kwargs):
            calls.append(("yf", period))
            return history

    monkeypatch.setattr(technical_analyzer.yf, "Ticker", lambda ticker: FakeTicker())
    monkeypatch.setattr(technical_analyzer.agent_state, "log_source_result", lambda *args, **kwargs: None)

    def _ohlcv(ticker, **kwargs):
        calls.append(kwargs.get("period"))
        return {"ok": True, "data": history, "provider": "yahoo", "fallback": False}

    monkeypatch.setattr("utils.equity_ohlcv.fetch_daily_ohlcv", _ohlcv)
    technical_analyzer.cache.delete("tech_v4_TEST_6mo", "info")
    technical_analyzer.cache.delete("tech_v4_TEST_1y", "info")

    technical_analyzer.compute_technical_indicators("TEST", period="6mo")
    technical_analyzer.compute_technical_indicators("TEST", period="1y")
    cached = technical_analyzer.compute_technical_indicators("TEST", period="1y")

    assert [period for period in calls if period in {"6mo", "1y"}] == ["6mo", "1y"]
    assert cached["technical_from_cache"] is True
    assert cached["technical_period"] == "1y"


def test_technical_rejects_quote_history_scale_mismatch(monkeypatch) -> None:
    index = pd.date_range("2025-01-01", periods=220, freq="B")
    history = pd.DataFrame({
        "Open": [980.0] * 220, "High": [990.0] * 220, "Low": [970.0] * 220,
        "Close": [980.0] * 220, "Volume": [1000] * 220,
    }, index=index)

    class FakeTicker:
        info = {"marketState": "CLOSED", "regularMarketPrice": 98.0, "regularMarketTime": 1780000000}

        def history(self, **kwargs):
            return history

    monkeypatch.setattr(technical_analyzer.yf, "Ticker", lambda ticker: FakeTicker())
    monkeypatch.setattr(
        "utils.equity_ohlcv.fetch_daily_ohlcv",
        lambda ticker, **kwargs: {"ok": True, "data": history, "provider": "yahoo", "fallback": False},
    )

    result = technical_analyzer.compute_technical_indicators("SCALE_TEST", period="1y", force_refresh=True)

    assert result["error"] == "price_scale_mismatch"
    assert result["quote_to_history_ratio"] == 0.1


def test_technical_records_polygon_bars_vendor(monkeypatch) -> None:
    index = pd.date_range("2025-01-01", periods=220, freq="B")
    history = pd.DataFrame({
        "Open": range(100, 320), "High": range(101, 321), "Low": range(99, 319),
        "Close": range(100, 320), "Volume": [1000] * 220,
    }, index=index)

    class FakeTicker:
        info = {"marketState": "CLOSED", "regularMarketPrice": 319}

    monkeypatch.setattr(technical_analyzer.yf, "Ticker", lambda ticker: FakeTicker())
    monkeypatch.setattr(technical_analyzer.agent_state, "log_source_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "utils.equity_ohlcv.fetch_daily_ohlcv",
        lambda ticker, **kwargs: {"ok": True, "data": history, "provider": "polygon", "fallback": False},
    )
    technical_analyzer.cache.delete("tech_v4_POLY_1y", "info")
    result = technical_analyzer.compute_technical_indicators("POLY", period="1y", force_refresh=True)
    assert result.get("error") is None
    assert result["technical_source"] == "polygon_aggs"
    assert result["bars_vendor"] == "polygon"
    assert result["bars_fallback"] is False
