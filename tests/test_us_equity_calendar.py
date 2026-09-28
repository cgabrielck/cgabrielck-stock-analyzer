"""US equity session calendar — no 24h/BTC mixing."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from backend.utils.us_equity_calendar import (
    CALENDAR_ID,
    equity_session_days,
    filter_us_equity_daily_bars,
    is_us_equity_trading_day,
    last_session_on_or_before,
    next_session_bars,
    nyse_holidays,
    status_payload,
)


def test_weekends_are_not_equity_sessions():
    assert is_us_equity_trading_day("2026-09-11") is True  # Friday
    assert is_us_equity_trading_day("2026-09-12") is False  # Saturday
    assert is_us_equity_trading_day("2026-09-13") is False  # Sunday
    assert is_us_equity_trading_day("2026-09-14") is True  # Monday


def test_thanksgiving_2026_is_closed():
    assert date(2026, 11, 26) in nyse_holidays(2026)
    assert is_us_equity_trading_day("2026-11-26") is False
    assert last_session_on_or_before("2026-11-26").date() == date(2026, 11, 25)


def test_independence_day_2026_observed_friday():
    # 4 Jul 2026 is Saturday → observed Friday 3 Jul
    assert is_us_equity_trading_day("2026-07-03") is False
    assert is_us_equity_trading_day("2026-07-04") is False


def test_good_friday_2026():
    assert is_us_equity_trading_day("2026-04-03") is False


def test_filter_drops_weekend_crypto_bars():
    idx = pd.to_datetime(["2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"])
    df = pd.DataFrame(
        {
            "Open": [1, 2, 3, 4],
            "High": [1, 2, 3, 4],
            "Low": [1, 2, 3, 4],
            "Close": [1, 2, 3, 4],
            "Volume": [10, 10, 10, 10],
        },
        index=idx,
    )
    out = filter_us_equity_daily_bars(df)
    assert list(out.index.strftime("%Y-%m-%d")) == ["2026-09-11", "2026-09-14"]


def test_equity_session_days_prefer_spy_not_btc_weekend():
    spy = pd.DataFrame(
        {"Close": [100.0, 101.0]},
        index=pd.to_datetime(["2026-09-11", "2026-09-14"]),
    )
    btc = pd.DataFrame(
        {"Close": [1.0, 2.0, 3.0, 4.0]},
        index=pd.to_datetime(["2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"]),
    )
    days = equity_session_days({"BTC-USD": btc}, spy=spy)
    assert [d.strftime("%Y-%m-%d") for d in days] == ["2026-09-11", "2026-09-14"]


def test_next_session_bars_skip_saturday():
    idx = pd.to_datetime(["2026-09-11", "2026-09-12", "2026-09-14"])
    df = pd.DataFrame(
        {"Open": [10, 11, 12], "High": [10, 11, 12], "Low": [10, 11, 12], "Close": [10, 11, 12]},
        index=idx,
    )
    nxt = next_session_bars(df, after="2026-09-11")
    assert list(nxt.index.strftime("%Y-%m-%d")) == ["2026-09-14"]


def test_status_payload_calendar_id():
    payload = status_payload(datetime(2026, 9, 16, 14, 0, tzinfo=ZoneInfo("America/New_York")))
    assert payload["calendar"] == CALENDAR_ID
    assert "24h/BTC" in payload["label"]
    assert payload["is_trading_day"] is True
