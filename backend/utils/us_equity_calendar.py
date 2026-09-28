"""US equity regular-session calendar (NYSE-style).

Backtests, shadow next-open lag, and daily OHLCV alignment use this calendar.
Do **not** mix 24h crypto (BTC) sessions into equity fill lag: Friday's signal
fills at Monday's regular open (or the next weekday that is not a holiday),
never Saturday/Sunday bars.

Regular session: 09:30–16:00 America/New_York, Monday–Friday minus NYSE holidays.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, time as dtime, timezone
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
CALENDAR_ID = "us_equity"
RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)

TimestampLike = Union[str, date, datetime, pd.Timestamp]


def calendar_label() -> str:
    return "US equity (NYSE RTH; not 24h/BTC)"


def _as_et(value: TimestampLike) -> datetime:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        # Naive daily bars are treated as session dates in US Eastern.
        return datetime(ts.year, ts.month, ts.day, tzinfo=ET)
    return ts.to_pydatetime().astimezone(ET)


def session_date(value: TimestampLike) -> date:
    """Calendar date of the US cash-equity session for a bar timestamp."""
    et = _as_et(value)
    return et.date()


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year, 12, 31)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    offset = (cursor.weekday() - weekday) % 7
    return cursor - timedelta(days=offset)


def _easter_gregorian(year: int) -> date:
    # Anonymous Gregorian algorithm (Western Easter).
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = ((h + el - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(holiday: date) -> date:
    if holiday.weekday() == 5:  # Saturday → Friday
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:  # Sunday → Monday
        return holiday + timedelta(days=1)
    return holiday


def nyse_holidays(year: int) -> set[date]:
    """Observed NYSE full-day closures for *year* (cash equities)."""
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),  # MLK
        _nth_weekday(year, 2, 0, 3),  # Presidents
        _easter_gregorian(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    if year >= 2021:
        holidays.add(_observed(date(year, 6, 19)))  # Juneteenth
    # New Year's Day falling on Saturday is observed the previous Friday (prior year).
    next_nyd = date(year + 1, 1, 1)
    if next_nyd.weekday() == 5:
        holidays.add(date(year, 12, 31))
    return holidays


def is_us_equity_trading_day(value: TimestampLike) -> bool:
    d = session_date(value)
    if d.weekday() >= 5:
        return False
    return d not in nyse_holidays(d.year)


def last_session_on_or_before(value: TimestampLike) -> pd.Timestamp:
    """Last NYSE session date on or before *value* (normalized, tz-naive)."""
    et = _as_et(value)
    cursor = et.date()
    for _ in range(14):
        if is_us_equity_trading_day(cursor):
            return pd.Timestamp(cursor)
        cursor = cursor - timedelta(days=1)
    return pd.Timestamp(et.date())


def next_session_on_or_after(value: TimestampLike) -> pd.Timestamp:
    et = _as_et(value)
    cursor = et.date()
    for _ in range(14):
        if is_us_equity_trading_day(cursor):
            return pd.Timestamp(cursor)
        cursor = cursor + timedelta(days=1)
    return pd.Timestamp(et.date())


def next_rth_open(now: Optional[datetime] = None) -> datetime:
    """Next (or current day's) regular-session open in America/New_York."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    et = current.astimezone(ET)
    cursor = et.date()
    for _ in range(14):
        if is_us_equity_trading_day(cursor):
            open_at = datetime.combine(cursor, RTH_OPEN, tzinfo=ET)
            if open_at > et or (cursor == et.date() and et.timetz().replace(tzinfo=None) < RTH_OPEN):
                return open_at
            if cursor == et.date() and RTH_OPEN <= et.timetz().replace(tzinfo=None) <= RTH_CLOSE:
                return open_at
        cursor = cursor + timedelta(days=1)
    return datetime.combine(et.date() + timedelta(days=1), RTH_OPEN, tzinfo=ET)


def in_regular_session(now: Optional[datetime] = None) -> bool:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    et = current.astimezone(ET)
    if not is_us_equity_trading_day(et):
        return False
    t = et.timetz().replace(tzinfo=None)
    return RTH_OPEN <= t <= RTH_CLOSE


def filter_us_equity_daily_bars(frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Drop weekend / NYSE-holiday / 24h-crypto dates from a daily OHLCV frame."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame() if frame is None else frame.iloc[0:0]
    out = frame.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        return out
    mask = [_is_session_index_value(idx) for idx in out.index]
    out = out.loc[mask]
    if out.empty:
        return out
    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is not None:
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    out.index = idx.normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def _is_session_index_value(idx: Any) -> bool:
    try:
        return is_us_equity_trading_day(idx)
    except Exception:
        return False


def equity_session_days(
    frames: Dict[str, pd.DataFrame],
    *,
    start: Optional[TimestampLike] = None,
    end: Optional[TimestampLike] = None,
    spy: Optional[pd.DataFrame] = None,
) -> List[pd.Timestamp]:
    """Canonical US session dates for a backtest.

    Prefer SPY's session index (US cash equity clock). Never union in weekend
    bars from 24h/BTC symbols.
    """
    spy_frame = spy
    if spy_frame is None:
        spy_frame = frames.get("SPY")
    source = spy_frame if spy_frame is not None and not spy_frame.empty else None
    if source is None:
        union: List[pd.Timestamp] = []
        seen = set()
        for frame in frames.values():
            filtered = filter_us_equity_daily_bars(frame)
            for idx in filtered.index:
                try:
                    ts = pd.Timestamp(idx)
                    if ts.tzinfo is not None:
                        ts = ts.tz_convert("America/New_York").tz_localize(None)
                    ts = ts.normalize()
                except Exception:
                    continue
                if ts not in seen:
                    seen.add(ts)
                    union.append(ts)
        dates = sorted(union)
    else:
        filtered = filter_us_equity_daily_bars(source)
        dates = []
        for idx in filtered.index:
            ts = pd.Timestamp(idx)
            if ts.tzinfo is not None:
                ts = ts.tz_convert("America/New_York").tz_localize(None)
            dates.append(ts.normalize())

    if start is not None:
        start_ts = pd.Timestamp(start).normalize()
        dates = [d for d in dates if d >= start_ts]
    if end is not None:
        end_ts = pd.Timestamp(end).normalize()
        dates = [d for d in dates if d <= end_ts]
    return [d for d in dates if is_us_equity_trading_day(d)]


def next_session_bars(
    frame: Optional[pd.DataFrame],
    *,
    after: Optional[TimestampLike] = None,
) -> pd.DataFrame:
    """US-session bars strictly after *after* (next-open lag, not wall-clock 24h)."""
    filtered = filter_us_equity_daily_bars(frame)
    if filtered.empty:
        return filtered
    if after is None:
        return filtered
    after_ts = pd.Timestamp(after)
    if filtered.index.tz is not None:
        if after_ts.tzinfo is None:
            after_ts = after_ts.tz_localize(filtered.index.tz)
        else:
            after_ts = after_ts.tz_convert(filtered.index.tz)
    elif after_ts.tzinfo is not None:
        after_ts = after_ts.tz_convert("America/New_York").tz_localize(None)
    return filtered.loc[filtered.index > after_ts]


def status_payload(now: Optional[datetime] = None) -> Dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    et = current if current.tzinfo else current.replace(tzinfo=timezone.utc)
    et = et.astimezone(ET)
    open_at = next_rth_open(current)
    return {
        "calendar": CALENDAR_ID,
        "label": calendar_label(),
        "timezone": "America/New_York",
        "session_date": session_date(et).isoformat(),
        "is_trading_day": is_us_equity_trading_day(et),
        "in_regular_session": in_regular_session(et),
        "next_rth_open": open_at.isoformat(),
        "rth": "09:30-16:00 ET",
    }
