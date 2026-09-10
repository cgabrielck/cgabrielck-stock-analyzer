"""Historical universe file shipped in repo is loadable."""
from datetime import date
from pathlib import Path

from backend.backtesting.universe import HistoricalUniverse, DEFAULT_HISTORY_PATH


def test_repo_historical_universe_file_exists_and_loads():
    assert DEFAULT_HISTORY_PATH.exists(), f"missing {DEFAULT_HISTORY_PATH}"
    universe = HistoricalUniverse(path=DEFAULT_HISTORY_PATH)
    assert universe.uses_current_universe_fallback is False
    assert universe.status()["historical_available"] is True
    jan = universe.tickers_for(date(2021, 1, 15))
    dec = universe.tickers_for(date(2021, 12, 15))
    assert "RIVN" not in jan
    assert "RIVN" in dec
    assert "AAPL" in jan
