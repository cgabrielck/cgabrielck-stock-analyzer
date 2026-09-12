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
        }
    )

    class FakeTicker:
        def history(self, **kwargs):
            return frame

    monkeypatch.setattr(polygon_equity, "is_configured", lambda: False)
    monkeypatch.setattr("yfinance.Ticker", lambda ticker: FakeTicker())

    worker = TradingWorker.__new__(TradingWorker)
    got = TradingWorker._fetch_ohlcv(worker, "AAPL")
    assert got is not None
    assert len(got) >= 30
