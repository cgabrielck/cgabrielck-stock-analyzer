import pandas as pd

import backend.agents.polygon_equity as polygon_equity
from backend.trading.engine.worker import TradingWorker


def test_placeholder_polygon_key_is_not_configured(monkeypatch):
    monkeypatch.setattr(
        polygon_equity,
        "POLYGON_API_KEY",
        "replace-with-your-polygon-or-massive-key",
    )
    assert polygon_equity.is_configured() is False


def test_fetch_ohlcv_uses_yahoo_when_polygon_off(monkeypatch):
    frame = pd.DataFrame(
        {
            "Open": list(range(40)),
            "High": list(range(40)),
            "Low": list(range(40)),
            "Close": list(range(40)),
            "Volume": [1000] * 40,
        },
        index=pd.bdate_range("2026-01-05", periods=40),
    )

    class FakeTicker:
        def history(self, **kwargs):
            return frame

    monkeypatch.setattr(polygon_equity, "is_configured", lambda: False)
    monkeypatch.setattr("yfinance.Ticker", lambda ticker: FakeTicker())

    worker = TradingWorker.__new__(TradingWorker)
    worker.last_ohlcv_vendor = None
    worker.last_ohlcv_fallback = False
    got = TradingWorker._fetch_ohlcv(worker, "AAPL")
    assert got is not None
    assert len(got) >= 30
    assert worker.last_ohlcv_vendor == "yahoo"
    assert worker.last_ohlcv_fallback is False


def test_fetch_ohlcv_prefers_polygon_when_keyed(monkeypatch):
    poly = pd.DataFrame(
        {
            "Open": list(range(40)),
            "High": list(range(40)),
            "Low": list(range(40)),
            "Close": list(range(40)),
            "Volume": [1000] * 40,
        },
        index=pd.bdate_range("2026-01-05", periods=40),
    )
    monkeypatch.setattr(polygon_equity, "is_configured", lambda: True)
    monkeypatch.setattr(
        polygon_equity,
        "fetch_daily_bars",
        lambda *a, **k: {"ok": True, "data": poly, "provider": "polygon"},
    )

    worker = TradingWorker.__new__(TradingWorker)
    worker.last_ohlcv_vendor = None
    worker.last_ohlcv_fallback = False
    got = TradingWorker._fetch_ohlcv(worker, "AAPL")
    assert got is not None
    assert worker.last_ohlcv_vendor == "polygon"
    assert worker.last_ohlcv_fallback is False


def test_fetch_ohlcv_labels_yahoo_fallback_when_polygon_fails(monkeypatch):
    yahoo = pd.DataFrame(
        {
            "Open": list(range(40)),
            "High": list(range(40)),
            "Low": list(range(40)),
            "Close": list(range(40)),
            "Volume": [1000] * 40,
        },
        index=pd.bdate_range("2026-01-05", periods=40),
    )
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
    worker = TradingWorker.__new__(TradingWorker)
    worker.last_ohlcv_vendor = None
    worker.last_ohlcv_fallback = False
    got = TradingWorker._fetch_ohlcv(worker, "AAPL")
    assert got is not None
    assert worker.last_ohlcv_vendor == "yahoo"
    assert worker.last_ohlcv_fallback is True


def test_fetch_next_bar_drops_weekend_crypto(monkeypatch):
    idx = pd.to_datetime(["2026-09-11", "2026-09-12", "2026-09-13"])
    frame = pd.DataFrame(
        {"Open": [10, 99, 11], "High": [10, 99, 11], "Low": [10, 99, 11], "Close": [10, 99, 11], "Volume": [1, 1, 1]},
        index=idx,
    )

    worker = TradingWorker.__new__(TradingWorker)
    worker.last_ohlcv_vendor = None
    worker.last_ohlcv_fallback = False
    monkeypatch.setattr(worker, "_fetch_ohlcv", lambda *a, **k: frame)
    nxt = TradingWorker._fetch_next_bar(worker, "AAPL", 10.0)
    assert list(nxt.index.strftime("%Y-%m-%d")) == ["2026-09-11"]
    assert float(nxt["Open"].iloc[0]) == 10.0
