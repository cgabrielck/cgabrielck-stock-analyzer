"""Closed v0 Desk indicator formulas — derived from OHLCV, never invented closes."""
from __future__ import annotations

import pandas as pd

from backend.utils.desk_indicators import bollinger, compute_closed_set, ema, macd, rsi_wilder, sma


def _close_frame(values):
    idx = pd.bdate_range("2026-01-05", periods=len(values))
    return pd.DataFrame(
        {
            "Open": values,
            "High": [v + 1 for v in values],
            "Low": [max(v - 1, 0.01) for v in values],
            "Close": values,
            "Volume": [1000] * len(values),
        },
        index=idx,
    )


def test_sma20_matches_window_mean():
    values = list(range(1, 41))
    close = pd.Series(values, dtype=float)
    series = sma(close, 20)
    assert pd.isna(series.iloc[18])
    assert abs(float(series.iloc[19]) - sum(values[:20]) / 20) < 1e-9
    assert abs(float(series.iloc[20]) - sum(values[1:21]) / 20) < 1e-9


def test_ema20_has_warmup_nulls_then_matches_pandas():
    close = pd.Series(range(1, 31), dtype=float)
    expected = close.ewm(span=20, adjust=False, min_periods=20).mean()
    got = ema(close, 20)
    assert pd.isna(got.iloc[18])
    assert abs(float(got.iloc[19]) - float(expected.iloc[19])) < 1e-9


def test_bollinger_middle_is_sma20_and_width_uses_std():
    close = pd.Series([float(i) for i in range(1, 31)])
    bands = bollinger(close, 20, 2)
    window = close.iloc[:20]
    assert abs(float(bands["middle"].iloc[19]) - float(window.mean())) < 1e-9
    width = float(bands["upper"].iloc[19] - bands["middle"].iloc[19])
    assert abs(width - 2 * float(window.std())) < 1e-9


def test_rising_series_rsi_is_hot_after_warmup():
    # Strictly rising closes make loss=0 and RSI undefined; keep small pullbacks.
    values = []
    price = 50.0
    for i in range(40):
        price += -0.4 if i % 6 == 0 else 1.6
        values.append(price)
    close = pd.Series(values)
    series = rsi_wilder(close, 14)
    assert pd.isna(series.iloc[13])
    assert float(series.iloc[-1]) > 70


def test_macd_shape_on_rising_series():
    close = pd.Series([float(i) for i in range(1, 80)])
    out = macd(close, 12, 26, 9)
    assert pd.isna(out["macd"].iloc[24])
    assert float(out["macd"].iloc[-1]) > 0
    assert abs(float(out["hist"].iloc[-1]) - (float(out["macd"].iloc[-1]) - float(out["signal"].iloc[-1]))) < 1e-9


def test_all_nan_close_yields_empty_indicator_lists():
    """M10: all-NaN Close must not invent SMA/RSI points (including v:0)."""
    idx = pd.bdate_range("2026-01-05", periods=30)
    frame = pd.DataFrame(
        {
            "Open": [float("nan")] * 30,
            "High": [float("nan")] * 30,
            "Low": [float("nan")] * 30,
            "Close": [float("nan")] * 30,
            "Volume": [1000] * 30,
        },
        index=idx,
    )
    series = compute_closed_set(frame)
    for key, points in series.items():
        assert points == [], key
        assert not any(p.get("v") == 0 for p in points)


def test_strictly_rising_rsi_does_not_invent_hundred():
    """S06: Wilder loss=0 → RSI undefined; do not invent v==100."""
    frame = _close_frame([float(i) for i in range(1, 40)])
    series = compute_closed_set(frame)
    for point in series["rsi14"]:
        assert point["v"] != 100
        assert point["v"] == point["v"]  # not NaN
    # Tip: every RSI point is skipped because loss=0 → NaN.
    assert series["rsi14"] == []


def test_sma50_warmup_point_count():
    """S07: n=60 → 11 SMA50 points; first t is index[49]."""
    frame = _close_frame(list(range(1, 61)))
    series = compute_closed_set(frame)
    assert len(series["sma50"]) == 11
    assert series["sma50"][0]["t"] == frame.index[49].strftime("%Y-%m-%d")
    assert series["sma50"][0]["v"] != 0 or series["sma50"][0]["v"] == round(sum(range(1, 51)) / 50, 4)


def test_missing_close_column_empty_set():
    """S08: frame without Close yields empty indicator lists, not invented closes."""
    idx = pd.bdate_range("2026-01-05", periods=20)
    frame = pd.DataFrame({"Open": list(range(1, 21)), "Volume": [1] * 20}, index=idx)
    series = compute_closed_set(frame)
    assert series["sma20"] == []
    assert series["rsi14"] == []
    assert series["macd"] == []


def test_compute_closed_set_skips_warmup_and_does_not_invent_empty():
    empty = compute_closed_set(pd.DataFrame())
    assert empty["sma20"] == []
    assert empty["rsi14"] == []
    frame = _close_frame(list(range(1, 61)))
    series = compute_closed_set(frame)
    assert series["sma20"][0]["v"] == round(sum(range(1, 21)) / 20, 4)
    assert series["bb_middle"][0]["v"] == series["sma20"][0]["v"]
    assert len(series["sma50"]) == 11  # 60 - 49
    dates = {p["t"] for p in series["sma20"]}
    assert series["sma20"][0]["t"] == frame.index[19].strftime("%Y-%m-%d")
    assert frame.index[0].strftime("%Y-%m-%d") not in dates
