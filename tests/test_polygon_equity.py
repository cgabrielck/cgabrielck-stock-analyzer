"""Tests for Polygon equity helper (offline / mocked)."""
from datetime import date
from unittest.mock import patch

import pandas as pd

from backend.agents import polygon_equity


def test_is_configured_false_without_key(monkeypatch):
    monkeypatch.setattr(polygon_equity, "POLYGON_API_KEY", "")
    assert polygon_equity.is_configured() is False


def test_fetch_daily_bars_maps_ohlcv(monkeypatch):
    monkeypatch.setattr(polygon_equity, "POLYGON_API_KEY", "test-key")

    def _fake_get(path, params=None, timeout=15):
        return {
            "results": [
                {"t": 1704067200000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100},
                {"t": 1704153600000, "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0, "v": 110},
            ]
        }

    monkeypatch.setattr(polygon_equity, "_get", _fake_get)
    result = polygon_equity.fetch_daily_bars("AAPL", start="2024-01-01", end="2024-01-03")
    assert result["ok"] is True
    assert isinstance(result["data"], pd.DataFrame)
    assert list(result["data"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(result["data"]) == 2


def test_fetch_ticker_list_date(monkeypatch):
    monkeypatch.setattr(polygon_equity, "POLYGON_API_KEY", "test-key")
    monkeypatch.setattr(
        polygon_equity,
        "_get",
        lambda path, params=None, timeout=15: {"results": {"list_date": "2021-11-10"}},
    )
    assert polygon_equity.fetch_ticker_list_date("RIVN") == date(2021, 11, 10)
