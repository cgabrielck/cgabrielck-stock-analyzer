"""Cadence switch: default 24h demo vs Monday rebalance + PANIC exception."""
from datetime import datetime, timezone

from backend.trading.engine.cadence import (
    CADENCE_INTRADAY,
    CADENCE_WEEKLY,
    allows_new_entries,
    is_us_monday,
    normalize_cadence,
    resolve_cadence,
)


# 2026-09-14 is a Monday; 2026-09-15 is a Tuesday.
_MONDAY_ET_SESSION = datetime(2026, 9, 14, 14, 30, tzinfo=timezone.utc)  # 10:30 ET
_TUESDAY_ET_SESSION = datetime(2026, 9, 15, 14, 30, tzinfo=timezone.utc)


def test_normalize_unknown_stays_intraday():
    assert normalize_cadence(None) == CADENCE_INTRADAY
    assert normalize_cadence("") == CADENCE_INTRADAY
    assert normalize_cadence("bogus") == CADENCE_INTRADAY
    assert normalize_cadence("weekly") == CADENCE_WEEKLY
    assert normalize_cadence("MONDAY") == CADENCE_WEEKLY


def test_resolve_default_is_intraday():
    assert resolve_cadence(None) == CADENCE_INTRADAY
    assert resolve_cadence({}) == CADENCE_INTRADAY
    assert resolve_cadence({"cadence": "weekly"}) == CADENCE_WEEKLY


def test_env_overrides_ai_mode(monkeypatch):
    monkeypatch.setenv("WORKER_CADENCE", "weekly")
    assert resolve_cadence({"cadence": "intraday"}) == CADENCE_WEEKLY
    monkeypatch.setenv("WORKER_CADENCE", "")
    monkeypatch.setenv("PAPER_CADENCE", "weekly")
    assert resolve_cadence({"cadence": "intraday"}) == CADENCE_WEEKLY
    monkeypatch.delenv("PAPER_CADENCE", raising=False)
    monkeypatch.delenv("WORKER_CADENCE", raising=False)
    assert resolve_cadence({"cadence": "intraday"}) == CADENCE_INTRADAY


def test_intraday_always_allows_entries():
    assert allows_new_entries("intraday", _TUESDAY_ET_SESSION, {"regime": "bull"}) is True
    assert allows_new_entries("intraday", _TUESDAY_ET_SESSION, {"regime": "high_volatility"}) is True


def test_weekly_monday_allows_even_in_bull():
    assert is_us_monday(_MONDAY_ET_SESSION) is True
    assert allows_new_entries("weekly", _MONDAY_ET_SESSION, {"regime": "bull"}) is True


def test_weekly_tuesday_blocks_unless_panic():
    assert is_us_monday(_TUESDAY_ET_SESSION) is False
    assert allows_new_entries("weekly", _TUESDAY_ET_SESSION, {"regime": "bull"}) is False
    assert allows_new_entries("weekly", _TUESDAY_ET_SESSION, {"regime": "bear"}) is False
    assert allows_new_entries("weekly", _TUESDAY_ET_SESSION, {"regime": "high_volatility"}) is True
    assert allows_new_entries("weekly", _TUESDAY_ET_SESSION, {"regime": "panic"}) is True


def _worker_account_and_bars():
    from unittest.mock import MagicMock

    from backend.trading.engine.worker import TradingWorker
    from backend.trading.models import AccountSummary

    close = [100 + i * 0.1 for i in range(40)]
    df = __import__("pandas").DataFrame(
        {
            "Open": close,
            "High": [c * 1.01 for c in close],
            "Low": [c * 0.99 for c in close],
            "Close": close,
            "Volume": [1_000_000] * 40,
        }
    )
    worker = TradingWorker(
        broker=MagicMock(),
        store=MagicMock(),
        manager=MagicMock(),
        reconciler=MagicMock(),
        strategy_id="stable",
        ticker_universe=["AAPL"],
    )
    worker.store.get_recent_orders.return_value = []
    account = AccountSummary(
        cash=100_000.0, buying_power=100_000.0, portfolio_value=100_000.0, positions=[]
    )
    return worker, account, df


def test_worker_weekly_cadence_waits_midweek():
    from datetime import datetime as real_dt
    from unittest.mock import patch

    worker, account, df = _worker_account_and_bars()

    class Frozen(real_dt):
        @classmethod
        def now(cls, tz=None):
            return _TUESDAY_ET_SESSION if tz is not None else _TUESDAY_ET_SESSION.replace(tzinfo=None)

    with patch.object(worker, "_detect_regime", return_value={"regime": "bull", "target_allocation": 0.90}), \
         patch.object(worker, "_fetch_vix", return_value=None), \
         patch.object(worker, "_fetch_ohlcv", return_value=df), \
         patch.object(worker, "_resolve_cadence", return_value="weekly"), \
         patch.object(worker, "_get_fundamental_score_info", return_value={"score": 80.0}), \
         patch.object(worker, "_get_top5_tickers", return_value=[]), \
         patch.object(worker, "_safe_get_account", return_value=account), \
         patch("backend.trading.engine.worker.datetime", Frozen):
        worker._run_strategy_signals(account)

    assert worker.last_skip_counts.get("cadence_wait", 0) >= 1
    assert "CADENCE_WAIT" in {row.get("action") for row in worker.last_signal_summary}


def test_worker_weekly_panic_exception_allows_entries_gate():
    from datetime import datetime as real_dt
    from unittest.mock import patch

    worker, account, df = _worker_account_and_bars()

    class Frozen(real_dt):
        @classmethod
        def now(cls, tz=None):
            return _TUESDAY_ET_SESSION if tz is not None else _TUESDAY_ET_SESSION.replace(tzinfo=None)

    with patch.object(worker, "_detect_regime", return_value={"regime": "high_volatility", "target_allocation": 0.40}), \
         patch.object(worker, "_fetch_vix", return_value=28.0), \
         patch.object(worker, "_fetch_ohlcv", return_value=df), \
         patch.object(worker, "_resolve_cadence", return_value="weekly"), \
         patch.object(worker, "_get_fundamental_score_info", return_value={"score": 80.0}), \
         patch.object(worker, "_get_top5_tickers", return_value=[]), \
         patch.object(worker, "_safe_get_account", return_value=account), \
         patch("backend.trading.engine.worker.datetime", Frozen):
        worker._run_strategy_signals(account)

    assert "CADENCE_WAIT" not in {row.get("action") for row in worker.last_signal_summary}
