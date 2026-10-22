"""Fail-case / honesty hard tests for Desk chart @ 79e3575 (tests only).

MUST M01–M09, M11–M20 plus SHOULD S01–S05 / S09–S12 and challenger
execution-semantics / SIG-NOORDER / STAMP / CROSS reinforcements.

Does not implement product M7, lab→OM, TradingView widget, or close #10.
Existing tests in ``test_desk_chart.py`` stay; this file only adds.
"""
from __future__ import annotations

import ast
import json
import math
import re
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.api import desk_chart
from tests.api.test_desk_chart import (
    APP_TSX,
    BANNED_IMPORTS,
    BANNED_NAMES,
    CHART_MODULES,
    INDEX,
    ROOT,
    _frame,
    _ok,
    imported_has_prefix,
    t_key_present,
)


STAMP_GUARD = 'if payload.get("verified") or payload.get("live"):'
STAMP_ERROR = 'raise RuntimeError("chart payload must not stamp verified/live")'
MONEY_PATH_LITERALS = (
    "place_desk_order",
    "OrderManager",
    "place_lab_manual_order",
    "advisory_debate",
    "submit_order",
    "submit_new_order",
)
NO_HISTORY_PHRASES = ("沒有歷史", "No history", "沒有歷史 K 線")
ALLOWED_PERIODS = ("1mo", "3mo", "6mo", "1y", "2y")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("backend.trading.telegram_inbox.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.scan_scheduler.start_background", lambda: (lambda: None))
    from backend.api.app import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def _fn_chunk(html: str, start_needle: str, end_needle: str) -> str:
    start = html.find(start_needle)
    end = html.find(end_needle)
    assert start != -1 and end != -1 and end > start, f"missing {start_needle!r} .. {end_needle!r}"
    return html[start:end]


def _i18n_values(html: str, key: str) -> tuple[str, str]:
    pattern = rf'"{re.escape(key)}":"((?:\\.|[^"\\])*)"'
    matches = re.findall(pattern, html)
    assert len(matches) >= 2, f"expected EN+ZH for {key}, got {matches!r}"
    return matches[0], matches[1]


def _assert_no_stamp(obj) -> None:
    if isinstance(obj, dict):
        assert obj.get("verified") is not True
        assert obj.get("live") is not True
        for value in obj.values():
            _assert_no_stamp(value)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_stamp(item)


def _run_node(script: str) -> str:
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout.strip()


def _chart_error_title_py(err):
    """Mirror of index.html chartErrorTitle — must match source (質疑者補強)."""
    if not isinstance(err, dict):
        err = {}
    msg = str(err["message"]) if err.get("message") is not None else ""
    if re.search(r"library failed|LightweightCharts", msg, re.I):
        return "chart.lib_error"
    status = err.get("status")
    if status == 404 or re.match(r"^No chart data$", msg, re.I):
        return "chart.empty"
    return "chart.load_error"


def _to_volume_py(bars):
    """Mirror of index.html toVolume filter — must match source (質疑者補強)."""
    out = []
    for bar in bars or []:
        if not bar or not bar.get("t"):
            continue
        volume = bar.get("v")
        if volume is None:
            continue
        try:
            number = float(volume)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        out.append({"time": bar["t"], "value": number})
    return out


# --- M01–M05 payload honesty -------------------------------------------------


def test_zero_volume_is_kept_not_null(client, monkeypatch):
    """M01: finite 0 volume is real volume, not missing."""
    frame = _frame(5)
    frame.loc[frame.index[2], "Volume"] = 0
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(frame))
    resp = client.get("/api/chart/AAPL")
    assert resp.status_code == 200
    bars = resp.json()["bars"]
    assert bars[2]["v"] == 0
    assert bars[2]["v"] is not None
    assert bars[1]["v"] == 1001
    assert bars[3]["v"] == 1003


def test_missing_volume_column_stays_null(client, monkeypatch):
    """M02: OHLC-only frame must still emit v:null, never coerce 0 / drop key."""
    frame = _frame(4).drop(columns=["Volume"])
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(frame))
    resp = client.get("/api/chart/AAPL")
    assert resp.status_code == 200
    bars = resp.json()["bars"]
    assert len(bars) == 4
    for bar in bars:
        assert "v" in bar
        assert bar["v"] is None


def test_non_finite_volume_stays_null(client, monkeypatch):
    """M03: ±inf / non-finite volume stay null; OHLC still ships."""
    frame = _frame(4)
    frame["Volume"] = pd.Series(
        [float("inf"), float("-inf"), "nan", 100],
        dtype=object,
        index=frame.index,
    )
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(frame))
    resp = client.get("/api/chart/AAPL")
    assert resp.status_code == 200
    volumes = [bar["v"] for bar in resp.json()["bars"]]
    assert volumes == [None, None, None, 100]
    for bar in resp.json()["bars"]:
        assert bar["c"] > 0


def test_ok_true_empty_frame_is_404_no_bars(client, monkeypatch):
    """M04: ok=True + empty DataFrame is still empty history, not 200+[]."""
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    monkeypatch.setattr(
        desk_chart,
        "fetch_daily_ohlcv",
        lambda *a, **k: _ok(empty, provider="yahoo"),
    )
    resp = client.get("/api/chart/AAPL")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["message"] == "No chart data"
    assert "bars" not in detail
    assert detail.get("verified") is not True
    assert detail.get("live") is not True


def test_partial_nan_ohlc_rows_skipped_not_invented(client, monkeypatch):
    """M05: skip bad OHLC rows; do not forward-fill close."""
    frame = _frame(5)
    bad_day = frame.index[2]
    frame.loc[bad_day, "Close"] = float("nan")
    frame.loc[frame.index[3], "Open"] = 0
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(frame))
    resp = client.get("/api/chart/AAPL")
    assert resp.status_code == 200
    bars = resp.json()["bars"]
    dates = [bar["t"] for bar in bars]
    assert bad_day.strftime("%Y-%m-%d") not in dates
    assert frame.index[3].strftime("%Y-%m-%d") not in dates
    assert len(bars) == 3
    assert bars[0]["c"] == 100.0
    assert bars[-1]["c"] == 104.0
    assert all(bar["c"] > 0 for bar in bars)


def test_fetch_exception_is_not_empty_history_404(client, monkeypatch):
    """M06: provider boom must not be labeled as empty history.

    Tip observation: uncaught RuntimeError → 500. If this ever becomes
    404 ``No chart data``, that is a product honesty bug (OPTIONAL FIX) —
    do not weaken this assert.
    """
    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", _boom)
    resp = client.get("/api/chart/AAPL")
    # Tip observation: uncaught RuntimeError is 500, often a blank/non-JSON body.
    # Dishonest success is 200 with bars, or 404 ``No chart data`` (empty history).
    assert resp.status_code != 200
    assert resp.status_code != 404
    assert "No chart data" not in resp.text
    if resp.content:
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            detail = body.get("detail", body)
            if isinstance(detail, dict):
                assert "bars" not in detail
                assert detail.get("message") != "No chart data"
    assert resp.status_code >= 500


# --- M07–M09 stamp guard -----------------------------------------------------


def test_success_payload_never_has_verified_or_live_keys(client, monkeypatch):
    """M07: 200 daily snapshot is not verified/live evidence."""
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(_frame(60)))
    resp = client.get("/api/chart/AAPL?period=6mo")
    assert resp.status_code == 200
    data = resp.json()
    assert "verified" not in data
    assert "live" not in data
    assert data["source_kind"] == "daily_snapshot"
    assert data["interval"] == "1d"
    _assert_no_stamp(data)


def test_empty_404_detail_never_stamps_verified_live(client, monkeypatch):
    """M08: empty-history 404 detail must not stamp verified/live or invent bars."""
    monkeypatch.setattr(
        desk_chart,
        "fetch_daily_ohlcv",
        lambda *a, **k: {
            "ok": False,
            "data": None,
            "provider": "none",
            "fallback": False,
            "attempted": ["yahoo"],
            "error": "empty",
            "reason": "yahoo_empty",
        },
    )
    resp = client.get("/api/chart/ZZZZ")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "verified" not in detail or detail.get("verified") is not True
    assert "live" not in detail or detail.get("live") is not True
    assert "bars" not in detail
    _assert_no_stamp(detail)


def test_chart_payload_runtime_guard_rejects_verified_live_stamp(monkeypatch):
    """M09 / STAMP: injecting verified or live into the assembled payload must raise."""
    source = Path(desk_chart.__file__).read_text(encoding="utf-8")
    assert STAMP_GUARD in source
    assert STAMP_ERROR in source
    fn_src = textwrap.dedent(__import__("inspect").getsource(desk_chart.chart_payload))
    assert STAMP_GUARD in fn_src

    def _inject_and_call(key: str) -> None:
        injected = fn_src.replace(
            STAMP_GUARD,
            f'payload[{key!r}] = True\n    {STAMP_GUARD}',
            1,
        )
        assert injected != fn_src
        assert f"payload[{key!r}] = True" in injected
        namespace = dict(vars(desk_chart))
        exec(injected, namespace)
        namespace["fetch_daily_ohlcv"] = lambda *a, **k: _ok(_frame(8))
        namespace["chart_payload"]("AAPL", period="6mo")

    with pytest.raises(RuntimeError, match="must not stamp verified/live"):
        _inject_and_call("verified")
    with pytest.raises(RuntimeError, match="must not stamp verified/live"):
        _inject_and_call("live")


# --- M11–M16 #38 UI honesty --------------------------------------------------


def test_chart_error_title_matrix_source():
    """M11 + S12 + 質疑者 ERR 執行語義表."""
    html = _html()
    helper = _fn_chunk(html, "function chartErrorTitle(", "function chartTheme(")
    assert "status === 404" in helper
    assert 't("chart.empty")' in helper
    assert 't("chart.load_error")' in helper
    assert 't("chart.lib_error")' in helper
    assert 'libFail ? t("chart.lib_error") : t("chart.empty")' not in html
    en_empty, zh_empty = _i18n_values(html, "chart.empty")
    en_load, zh_load = _i18n_values(html, "chart.load_error")
    en_lib, zh_lib = _i18n_values(html, "chart.lib_error")
    assert "No history" in en_empty
    assert "沒有歷史" in zh_empty or "此代碼沒有歷史" in zh_empty
    for value in (en_load, zh_load, en_lib, zh_lib):
        for phrase in NO_HISTORY_PHRASES:
            assert phrase not in value
    assert "Chart failed to load." in en_load
    assert "圖表載入失敗。" in zh_load

    cases = [
        ({"status": 404, "message": "No chart data"}, "chart.empty"),
        ({"status": 404, "message": "other"}, "chart.empty"),
        ({"status": 500, "message": "upstream"}, "chart.load_error"),
        ({"status": 502, "message": "bad gateway"}, "chart.load_error"),
        ({"status": 400, "message": "Unsupported period"}, "chart.load_error"),
        ({"message": "network"}, "chart.load_error"),
        ({"message": "Chart library failed to load"}, "chart.lib_error"),
        ({"message": "LightweightCharts is not defined"}, "chart.lib_error"),
        ({}, "chart.load_error"),
        (None, "chart.load_error"),
    ]
    for err, expected in cases:
        assert _chart_error_title_py(err) == expected, (err, expected)

    js_cases = json.dumps([err if err is not None else {} for err, _ in cases[:-1]])
    script = (
        "function t(k){return k;}\n"
        + helper
        + "\nconst cases = "
        + js_cases
        + ";\n"
        + "console.log(JSON.stringify(cases.map(e => chartErrorTitle(e))));\n"
        + "console.log(JSON.stringify(chartErrorTitle(null)));\n"
    )
    out = _run_node(script).splitlines()
    keys = json.loads(out[0])
    expected_js = [exp for err, exp in cases[:-1]]
    assert keys == expected_js
    assert json.loads(out[1]) == "chart.empty" or json.loads(out[1]) == "chart.load_error"
    # JS null err: msg="", status undefined → load_error (not empty).
    assert json.loads(out[1]) == "chart.load_error"


def test_chart_catch_uses_error_title_helper():
    """M12: catch must call chartErrorTitle(e); must not default non-404 to empty."""
    html = _html()
    chart_fn = _fn_chunk(html, "async function chart(", "async function aimode(")
    catch = chart_fn[chart_fn.find("catch(") :]
    assert "chartErrorTitle(e)" in catch
    assert 't("chart.not_live")' in catch
    assert catch.count('t("chart.empty")') == 0
    assert 'libFail ? t("chart.lib_error") : t("chart.empty")' not in html


def test_to_volume_omits_null_keeps_zero_logic():
    """M13 + N02: toVolume drops null/NaN/inf, keeps true 0 (node execution)."""
    html = _html()
    chunk = _fn_chunk(html, "function toVolume(", "function chartErrorTitle(")
    assert "b.v!=null && isFinite(Number(b.v))" in chunk
    assert "b.v==null?0" not in html
    assert "b.v == null ? 0" not in html
    assert "value: b.v==null?0" not in html

    bars = [
        {"t": "2026-01-05", "v": None, "o": 1, "c": 1},
        {"t": "2026-01-06", "v": 0, "o": 1, "c": 1},
        {"t": "2026-01-07", "v": float("nan"), "o": 1, "c": 1},
        {"t": "2026-01-08", "v": 1, "o": 1, "c": 1},
        {"t": "2026-01-09", "v": float("inf"), "o": 1, "c": 1},
        {"t": "2026-01-10", "v": 0.0, "o": 1, "c": 1},
        {"t": "2026-01-11", "v": 1500, "o": 1, "c": 1},
    ]
    py_out = _to_volume_py(bars)
    assert [row["time"] for row in py_out] == [
        "2026-01-06",
        "2026-01-08",
        "2026-01-10",
        "2026-01-11",
    ]
    assert 0 in [row["value"] for row in py_out]
    assert all(row["value"] != 0 or row["time"] in {"2026-01-06", "2026-01-10"} for row in py_out)

    js_bars = json.dumps(
        [
            {"t": "2026-01-05", "v": None, "c": 1, "o": 1},
            {"t": "2026-01-06", "v": 0, "c": 1, "o": 1},
            {"t": "2026-01-07", "v": None, "c": 1, "o": 1},
            {"t": "2026-01-08", "v": 1, "c": 1, "o": 1},
            {"t": "2026-01-09", "v": "nan", "c": 1, "o": 1},
            {"t": "2026-01-10", "v": 0.0, "c": 1, "o": 1},
            {"t": "2026-01-11", "v": 1500, "c": 1, "o": 1},
        ]
    )
    script = chunk + f"\nconsole.log(JSON.stringify(toVolume({js_bars})));\n"
    result = json.loads(_run_node(script))
    times = [row["time"] for row in result]
    assert "2026-01-05" not in times
    assert "2026-01-07" not in times
    assert "2026-01-09" not in times
    assert times == ["2026-01-06", "2026-01-08", "2026-01-10", "2026-01-11"]
    zeros = [row["value"] for row in result if row["value"] == 0]
    assert zeros == [0, 0]


def test_fmt_vol_null_is_em_dash_not_zero():
    """M14: legend missing volume is em dash, not V 0."""
    html = _html()
    fmt = _fn_chunk(html, "function fmtVol(n){", "function indMap(")
    assert "n==null||isNaN(n)" in fmt.replace(" ", "")
    assert '"—"' in fmt
    legend = _fn_chunk(html, "const writeLegend = (time) => {", "writeLegend(null);")
    assert "fmtVol(b.v)" in legend
    assert "b.v||0" not in legend
    assert 'V ${fmtVol(b.v)}' in legend or "V ${fmtVol(b.v)}" in html
    null_branch = fmt[fmt.find("if(") : fmt.find("const v")]
    assert "return" in null_branch
    assert 'return "0"' not in null_branch
    assert "return 0" not in null_branch


def test_chart_ui_empty_bars_branch_uses_chart_empty():
    """M15: success payload with no bars uses chart.empty and does not paint."""
    html = _html()
    chart_fn = _fn_chunk(html, "async function chart(", "async function aimode(")
    empty_idx = chart_fn.find("if(!bars.length)")
    assert empty_idx != -1
    empty_branch = chart_fn[empty_idx : chart_fn.find("const fallback")]
    assert 't("chart.empty")' in empty_branch
    assert 't("chart.not_live")' in empty_branch
    assert "paintDeskChart" not in empty_branch
    assert "addCandlestickSeries" not in empty_branch
    assert "setData(bars)" not in empty_branch
    assert "paintDeskChart(d)" in chart_fn[chart_fn.find("const fallback") :]


def test_chart_honesty_captions_bilingual():
    """M16: bilingual not-TV / not-live / not-order; no official widget claims."""
    html = _html()
    for key in ("chart.not_tv", "chart.not_live", "chart.not_order"):
        assert t_key_present(html, key)
        en, zh = _i18n_values(html, key)
        assert en.strip()
        assert zh.strip()
    en_tv, zh_tv = _i18n_values(html, "chart.not_tv")
    assert "Not an official TradingView widget" in en_tv
    assert "不是官方 TradingView widget" in zh_tv
    lower = html.lower()
    assert "tradingview.com/widget" not in lower
    assert "s.tradingview.com" not in lower
    assert "TradingView integrated" not in html
    assert "verified: true" not in html
    assert "live: true" not in html


# --- M17–M20 isolation -------------------------------------------------------


def test_chart_modules_ban_money_path_names():
    """M17: chart python modules stay disjoint from OM / advisory / submit."""
    extra = BANNED_NAMES | {
        "OrderManager",
        "place_desk_order",
        "place_lab_manual_order",
        "submit_order",
        "submit_new_order",
        "advisory_debate",
    }
    for path in CHART_MODULES:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        imported = set()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
                    names.add(alias.asname or alias.name.split(".")[-1])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                for alias in node.names:
                    names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
        assert not any(mod in imported or imported_has_prefix(imported, mod) for mod in BANNED_IMPORTS)
        assert names.isdisjoint(extra)
        # Docstrings may mention OrderManager as a prohibition; ban call-site literals.
        for needle in ("place_desk_order", "place_lab_manual_order", "submit_new_order", "advisory_debate"):
            assert needle not in text


def test_app_chart_route_only_delegates_to_chart_payload():
    """M18: GET /api/chart/{symbol} only delegates to chart_payload."""
    path = ROOT / "backend/api/app.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    fn = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not dec.args:
                continue
            arg0 = dec.args[0]
            if isinstance(arg0, ast.Constant) and arg0.value == "/api/chart/{symbol}":
                fn = node
    assert fn is not None
    segment = ast.get_source_segment(text, fn) or ""
    assert "chart_payload" in segment
    for banned in MONEY_PATH_LITERALS + ("advisory",):
        assert banned not in segment
    called = set()
    imported = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            for alias in node.names:
                called.add(alias.name)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert "chart_payload" in called
    assert "backend.api.desk_chart" in imported
    assert called.isdisjoint({"place_desk_order", "OrderManager", "advisory_debate"})


def test_chart_get_does_not_call_place_desk_order(client, monkeypatch):
    """M19: reading a chart must not submit a desk order."""
    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("chart GET must not call place_desk_order")

    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(_frame(20)))
    monkeypatch.setattr("backend.trading.engine.desk_orders.place_desk_order", _spy)
    monkeypatch.setattr("backend.api.app.place_desk_order", _spy)
    resp = client.get("/api/chart/AAPL")
    assert resp.status_code == 200
    assert calls == []
    assert resp.json()["symbol"] == "AAPL"


def test_chart_ui_chunk_bans_money_path_and_buy_sell_wire():
    """M20 + OM-01: chart HTML chunk has candles/crosshair, not order wire."""
    html = _html()
    chunk = _fn_chunk(html, "function loadChartInd(", "async function aimode(")
    for banned in MONEY_PATH_LITERALS + ("/api/orders", "api(\"/api/orders"):
        assert banned not in chunk
    assert "addCandlestickSeries" in chunk
    assert "subscribeCrosshairMove" in chunk
    assert "lightweight-charts" in chunk
    src = APP_TSX.read_text(encoding="utf-8")
    page_start = src.find("function ChartPage")
    page_end = src.find("function AiMode()", page_start)
    page = src[page_start:page_end]
    assert page_start != -1
    for banned in ("place_desk", "/api/orders", "OrderManager", "advisory_debate"):
        assert banned not in page
    assert "do not place orders" in page
    assert "Not live / not verified" in page


def test_no_official_tradingview_widget_or_embed(client):
    """S09 / TV-01: vendor LICENSE + UI deny official TradingView widget."""
    html = _html()
    license_path = ROOT / "web/static/vendor/LICENSE.lightweight-charts.txt"
    text = license_path.read_text(encoding="utf-8")
    collapsed = " ".join(text.split())
    assert "not an official TradingView widget" in collapsed
    assert "Apache" in text
    assert "tradingview.com/widget" not in html.lower()
    assert "s.tradingview.com" not in html.lower()
    assert "TradingView integrated" not in html
    assert t_key_present(html, "chart.not_tv")
    resp = client.get("/static/vendor/lightweight-charts.standalone.production.js")
    assert resp.status_code == 200
    assert "LightweightCharts" in resp.text


def test_react_chart_page_keeps_honesty_captions_no_verified_literal():
    """S10: React ChartPage captions; no verified:true literal."""
    src = APP_TSX.read_text(encoding="utf-8")
    assert "Not live / not verified" in src
    assert "Not an official TradingView widget" in src
    assert "do not place orders" in src
    assert "verified: true" not in src
    assert "live: true" not in src


def test_invalid_period_maps_to_load_error_not_empty_in_helper():
    """S12: chartErrorTitle(status 400) is load_error, not empty."""
    assert _chart_error_title_py({"status": 400, "message": "bad period"}) == "chart.load_error"
    assert _chart_error_title_py({"status": 404}) == "chart.empty"


# --- SHOULD API edges --------------------------------------------------------


@pytest.mark.parametrize("period", ALLOWED_PERIODS)
def test_allowed_periods_accepted(client, monkeypatch, period):
    """S01: allowed periods echo back; none silently remapped."""
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(_frame(30)))
    resp = client.get(f"/api/chart/AAPL?period={period}")
    assert resp.status_code == 200
    assert resp.json()["period"] == period


def test_symbol_too_long_or_blank_is_404(client, monkeypatch):
    """S02: overlong / blank / tab symbols are 404 without invented bars."""
    from urllib.parse import quote

    monkeypatch.setattr(
        desk_chart,
        "fetch_daily_ohlcv",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch invalid symbol")),
    )
    for raw in ("A" * 17, "   ", "\t\t"):
        resp = client.get(f"/api/chart/{quote(raw)}")
        assert resp.status_code == 404, raw
        detail = resp.json()["detail"]
        assert "bars" not in detail


def test_non_positive_ohlc_rows_skipped(client, monkeypatch):
    """S03: Close=0 / negative OHLC rows are skipped; all-bad → 404."""
    mixed = _frame(3)
    mixed.loc[mixed.index[1], "Close"] = 0
    mixed.loc[mixed.index[2], ["Open", "High", "Low", "Close"]] = -1
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(mixed))
    resp = client.get("/api/chart/AAPL")
    assert resp.status_code == 200
    bars = resp.json()["bars"]
    assert len(bars) == 1
    assert bars[0]["c"] == 100.0

    bad = _frame(2)
    bad[["Open", "High", "Low", "Close"]] = 0
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(bad))
    empty = client.get("/api/chart/ZERO")
    assert empty.status_code == 404
    assert "bars" not in empty.json()["detail"]


def test_indicators_do_not_require_invented_bar_alignment(client, monkeypatch):
    """S04: skipped OHLC days must not be invented as bars (record indicator as-is)."""
    frame = _frame(40)
    skipped = frame.index[10]
    frame.loc[skipped, "Close"] = float("nan")
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(frame))
    data = client.get("/api/chart/AAPL").json()
    bar_dates = {bar["t"] for bar in data["bars"]}
    skip_s = skipped.strftime("%Y-%m-%d")
    assert skip_s not in bar_dates
    # Indicators are derived from the raw frame, not back-filled bars.
    # Current tip: NaN close is omitted from indicator points (not invented as v:0).
    for series in data["indicators"].values():
        for point in series:
            if point["t"] == skip_s:
                assert point.get("v") not in (0, 0.0)
                assert point.get("v") is not None
                assert math.isfinite(float(point["v"]))


def test_fallback_metadata_round_trip(client, monkeypatch):
    """S05 / PROV-01: yahoo fallback is labeled, never verified/live."""
    monkeypatch.setattr(
        desk_chart,
        "fetch_daily_ohlcv",
        lambda *a, **k: _ok(
            _frame(20),
            provider="yahoo",
            fallback=True,
            reason="yahoo_fallback",
            attempted=["polygon", "yahoo"],
        ),
    )
    data = client.get("/api/chart/MSFT").json()
    assert data["fallback"] is True
    assert data["fallback_reason"] == "yahoo_fallback"
    assert data["attempted"] == ["polygon", "yahoo"]
    assert data["source_kind"] == "daily_snapshot"
    assert data.get("verified") is not True
    assert data.get("live") is not True
    assert "verified" not in data


# --- SIG-NOORDER / CROSS / NICE ---------------------------------------------


def test_no_backend_signals_package_and_no_signals_api_route(client):
    """SIG-NOORDER-01: absence of M5 product — not worker last_signals."""
    assert not (ROOT / "backend/signals").exists()
    from backend.api.app import app

    paths = [getattr(route, "path", "") for route in app.routes]
    assert not any(str(path).startswith("/api/signals") for path in paths)
    resp = client.get("/api/signals")
    assert resp.status_code in {404, 405}
    assert resp.status_code != 200


def test_loading_chart_does_not_flip_worker_verified_chip(client, monkeypatch, tmp_path):
    """CROSS-01: GET /api/chart must not flip worker verified / linked ON."""
    monkeypatch.setenv("APCA_PAPER", "true")
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "unit-test-operator")
    client.headers.update({"X-Desk-Operator-Token": "unit-test-operator"})
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(_frame(12)))
    from backend.api import worker_ctl
    from backend.api.desk_display import display_chips

    hb = tmp_path / "worker_heartbeat.json"
    pidf = tmp_path / "worker.pid"
    hb.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(worker_ctl, "HEARTBEAT_PATH", hb)
    monkeypatch.setattr(worker_ctl, "PID_PATH", pidf)
    monkeypatch.setattr(worker_ctl, "pid_alive", lambda _pid: False)

    before = client.get("/api/worker/status")
    assert before.status_code == 200, before.text
    before_body = before.json()
    assert before_body.get("verified") is not True
    assert before_body.get("display", {}).get("verified") is not True
    assert "linked" not in before_body.get("display", {}).get("worker_label", "").lower()

    chart = client.get("/api/chart/AAPL")
    assert chart.status_code == 200
    assert chart.json().get("verified") is not True

    after = client.get("/api/worker/status")
    assert after.status_code == 200
    after_body = after.json()
    assert after_body.get("verified") is not True
    assert after_body.get("live") is not True
    chips = after_body.get("display") or display_chips(after_body)
    assert chips.get("verified") is not True
    assert chips.get("live") is not True
    assert "linked" not in str(chips.get("worker_label", "")).lower()
    assert chips.get("worker_label") != "Worker linked ON"


def test_negative_volume_policy_documented(client, monkeypatch):
    """N01: current tip keeps finite negative volume (not a must-fix)."""
    frame = _frame(2)
    frame.loc[frame.index[0], "Volume"] = -15
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(frame))
    data = client.get("/api/chart/AAPL").json()
    assert data["bars"][0]["v"] == -15


def test_fetched_at_is_iso_z(client, monkeypatch):
    """N03: fetched_at is UTC Z, not a live stamp."""
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(_frame(8)))
    data = client.get("/api/chart/AAPL").json()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T.*Z$", data["fetched_at"])
    assert data.get("verified") is not True


def test_high_less_than_low_is_not_rewritten(client, monkeypatch):
    """N04: inverted high/low still ships as given when all positive — do not invent a fix."""
    frame = _frame(1)
    frame["High"] = 90.0
    frame["Low"] = 110.0
    frame["Open"] = 100.0
    frame["Close"] = 100.0
    monkeypatch.setattr(desk_chart, "fetch_daily_ohlcv", lambda *a, **k: _ok(frame))
    bar = client.get("/api/chart/AAPL").json()["bars"][0]
    assert bar["h"] == 90.0
    assert bar["l"] == 110.0
