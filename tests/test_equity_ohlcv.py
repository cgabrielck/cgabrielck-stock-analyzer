"""Shared OHLCV vendor selector — Polygon preferred, Yahoo labeled fallback."""
import pandas as pd

from backend.agents import polygon_equity
from backend.utils import equity_ohlcv


def _frame(n=40):
    idx = pd.bdate_range("2026-01-05", periods=n)
    return pd.DataFrame(
        {
            "Open": list(range(n)),
            "High": list(range(n)),
            "Low": list(range(n)),
            "Close": list(range(n)),
            "Volume": [1000] * n,
        },
        index=idx,
    )


def test_fetch_daily_ohlcv_prefers_polygon_when_keyed(monkeypatch):
    poly = _frame(40)
    monkeypatch.setattr(polygon_equity, "is_configured", lambda: True)

    def _bars(ticker, **kwargs):
        return {"ok": True, "data": poly, "provider": "polygon"}

    monkeypatch.setattr(polygon_equity, "fetch_daily_bars", _bars)
    result = equity_ohlcv.fetch_daily_ohlcv("AAPL", period="1y", min_bars=30)
    assert result["ok"] is True
    assert result["provider"] == "polygon"
    assert result["fallback"] is False
    assert len(result["data"]) >= 30


def test_fetch_daily_ohlcv_yahoo_fallback_labeled(monkeypatch):
    yahoo = _frame(40)
    monkeypatch.setattr(polygon_equity, "is_configured", lambda: True)
    monkeypatch.setattr(
        polygon_equity,
        "fetch_daily_bars",
        lambda *a, **k: {"ok": False, "error": "empty", "reason": "no_bars"},
    )

    class FakeTicker:
        def history(self, **kwargs):
            return yahoo

    monkeypatch.setattr("yfinance.Ticker", lambda ticker: FakeTicker())
    result = equity_ohlcv.fetch_daily_ohlcv("AAPL", period="1y", min_bars=30)
    assert result["ok"] is True
    assert result["provider"] == "yahoo"
    assert result["fallback"] is True
    assert result["reason"] == "yahoo_fallback"


def test_fetch_daily_ohlcv_yahoo_primary_without_key(monkeypatch):
    yahoo = _frame(40)
    monkeypatch.setattr(polygon_equity, "is_configured", lambda: False)

    class FakeTicker:
        def history(self, **kwargs):
            return yahoo

    monkeypatch.setattr("yfinance.Ticker", lambda ticker: FakeTicker())
    result = equity_ohlcv.fetch_daily_ohlcv("MSFT", period="1y", min_bars=30)
    assert result["ok"] is True
    assert result["provider"] == "yahoo"
    assert result["fallback"] is False


def test_fetch_daily_ohlcv_drops_weekend_bars(monkeypatch):
    idx = pd.to_datetime(
        ["2026-09-11", "2026-09-12", "2026-09-14"] + [f"2026-08-{d:02d}" for d in range(3, 31)]
    )
    raw = pd.DataFrame(
        {
            "Open": [1] * len(idx),
            "High": [1] * len(idx),
            "Low": [1] * len(idx),
            "Close": [1] * len(idx),
            "Volume": [10] * len(idx),
        },
        index=idx,
    )
    monkeypatch.setattr(polygon_equity, "is_configured", lambda: True)
    monkeypatch.setattr(
        polygon_equity,
        "fetch_daily_bars",
        lambda *a, **k: {"ok": True, "data": raw, "provider": "polygon"},
    )
    result = equity_ohlcv.fetch_daily_ohlcv("AAPL", period="1y", min_bars=1)
    assert result["ok"] is True
    dates = set(result["data"].index.strftime("%Y-%m-%d"))
    assert "2026-09-12" not in dates
    assert "2026-09-11" in dates
