"""QuantStats tear sheet for paper/shadow books and single tickers — report only.

Never places orders. Missing quantstats degrades to a JSON metrics stub.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from backend.lab.flags import quantstats_enabled
from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

_LATEST_NAME = "lab_tear_latest.json"


def _latest_path() -> Path:
    return Path(DATA_DIR) / _LATEST_NAME


def _stub_path() -> Path:
    return Path(DATA_DIR) / "lab_quantstats_stub.json"


def _html_path(scope: str, ticker: Optional[str] = None) -> Path:
    if scope == "ticker" and ticker:
        safe = "".join(ch for ch in ticker.upper() if ch.isalnum() or ch in ("-", "_"))[:12]
        return Path(DATA_DIR) / f"lab_quantstats_tear_{safe}.html"
    return Path(DATA_DIR) / "lab_quantstats_tear.html"


def _equity_to_returns(equity_curve: List[Dict[str, Any]]) -> Optional[pd.Series]:
    if not equity_curve or len(equity_curve) < 3:
        return None
    rows = []
    for row in equity_curve:
        d = row.get("date") or row.get("ts")
        eq = row.get("equity")
        if d is None or eq is None:
            continue
        rows.append((pd.Timestamp(d), float(eq)))
    if len(rows) < 3:
        return None
    s = pd.Series({d: e for d, e in rows}).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    rets = s.pct_change().dropna()
    return rets if len(rets) >= 2 else None


def series_to_curve(close: pd.Series) -> List[Dict[str, Any]]:
    """Treat a close-price series as an equity-like curve for QuantStats."""
    out: List[Dict[str, Any]] = []
    if close is None or len(close) == 0:
        return out
    s = close.dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    for idx, val in s.items():
        try:
            ts = pd.Timestamp(idx)
            day = str(ts.date())
        except Exception:
            day = str(idx)[:10]
        out.append({"date": day, "equity": float(val)})
    return out


def summary_metrics(
    rets: pd.Series,
    spy_returns: Optional[pd.Series] = None,
    *,
    engine: str = "json_stub",
) -> Dict[str, Any]:
    """Always-on metrics so Desk can render without the quantstats package."""
    n = int(len(rets))
    total = float((1 + rets).prod() - 1) if n else 0.0
    vol = float(rets.std() * (252 ** 0.5)) if n > 1 else None
    ann = float(rets.mean() * 252) if n else None
    sharpe = None
    if vol and vol > 0 and ann is not None:
        sharpe = round(float(ann / vol), 3)
    cum = (1 + rets).cumprod()
    peak = cum.cummax()
    dd = float((cum / peak - 1).min()) if n else 0.0
    summary: Dict[str, Any] = {
        "n_days": n,
        "total_return_pct": round(total * 100, 2),
        "vol_pct": round(vol * 100, 2) if vol is not None else None,
        "sharpe": sharpe,
        "max_drawdown_pct": round(dd * 100, 2),
        "engine": engine,
        "quantstats_flag": quantstats_enabled(),
        "spy_return_pct": None,
        "alpha_pct": None,
    }
    if spy_returns is not None and len(spy_returns) > 2:
        aligned = pd.concat(
            [rets.rename("p"), spy_returns.rename("spy")], axis=1, join="inner"
        ).dropna()
        if len(aligned) >= 3:
            spy_tot = float((1 + aligned["spy"]).prod() - 1)
            p_tot = float((1 + aligned["p"]).prod() - 1)
            summary["spy_return_pct"] = round(spy_tot * 100, 2)
            summary["alpha_pct"] = round((p_tot - spy_tot) * 100, 2)
            summary["aligned_days"] = int(len(aligned))
    return summary


def load_close_series(
    ticker: str,
    *,
    period: str = "1y",
) -> Tuple[Optional[pd.Series], str, List[str]]:
    """Polygon first, Yahoo fallback. Never raises to the Desk."""
    notes: List[str] = []
    sym = (ticker or "").upper().strip()
    if not sym:
        return None, "none", ["missing_ticker"]

    try:
        from backend.agents.polygon_equity import fetch_chart_data_polygon, is_configured

        if is_configured():
            out = fetch_chart_data_polygon(sym, period)
            data = out.get("data")
            if data is not None and getattr(data, "empty", True) is False and "Close" in data.columns:
                return data["Close"], "polygon", notes
            notes.append(str(out.get("reason") or out.get("error") or "polygon_empty"))
    except Exception as exc:
        notes.append(f"polygon:{exc}")

    try:
        import yfinance as yf

        hist = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=True)
        if hist is not None and not hist.empty and "Close" in hist.columns:
            return hist["Close"], "yahoo", notes
        notes.append("yahoo_empty")
    except Exception as exc:
        notes.append(f"yahoo:{exc}")
    return None, "none", notes


def load_spy_returns(index: Optional[pd.Index] = None) -> Optional[pd.Series]:
    close, vendor, _notes = load_close_series("SPY")
    if close is None or len(close) < 5:
        return None
    rets = close.pct_change().dropna()
    rets.name = "spy"
    if index is not None:
        rets = rets.reindex(index).dropna()
    logger.debug("SPY returns vendor=%s n=%s", vendor, len(rets))
    return rets if len(rets) >= 3 else None


def load_paper_equity_curve() -> Tuple[List[Dict[str, Any]], List[str]]:
    notes: List[str] = []
    curve_rows: List[Dict[str, Any]] = []
    try:
        from backend.api.paper_performance import _load_paper_orders
        from backend.trading.performance.tracker import INITIAL_CAPITAL, compute_equity_curve

        orders = _load_paper_orders()
        df = compute_equity_curve(orders, INITIAL_CAPITAL)
        if df is not None and len(df) and "equity" in df.columns:
            for _, row in df.iterrows():
                curve_rows.append({"date": str(row.get("date")), "equity": float(row["equity"])})
        else:
            notes.append("empty_paper_curve")
    except Exception as exc:
        notes.append(f"paper_curve:{exc}")
    return curve_rows, notes


def persist_latest(payload: Dict[str, Any]) -> Path:
    path = _latest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_latest_tear(*, ticker: Optional[str] = None) -> Dict[str, Any]:
    path = _latest_path()
    if not path.exists():
        return {
            "available": False,
            "complete": False,
            "scope": "ticker" if ticker else "paper",
            "ticker": (ticker or "").upper() or None,
            "notes": ["no_cached_tear"],
            "places_order": False,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"available": False, "complete": False, "notes": ["cache_corrupt"], "places_order": False}
    data.setdefault("places_order", False)
    return data


def build_tear_sheet(
    *,
    equity_curve: List[Dict[str, Any]],
    spy_returns: Optional[pd.Series] = None,
    title: str = "Cgab paper/shadow",
    out_html: Optional[Path] = None,
    scope: str = "paper",
    ticker: Optional[str] = None,
    vendor: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate QuantStats HTML if enabled+installed; else a JSON metrics stub."""
    out_html = out_html or _html_path(scope, ticker)
    result: Dict[str, Any] = {
        "available": False,
        "engine": None,
        "path": str(out_html),
        "complete": False,
        "notes": [],
        "scope": scope,
        "ticker": (ticker or "").upper() or None,
        "vendor": vendor,
        "html_available": False,
        "places_order": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "curve_tail": equity_curve[-40:] if equity_curve else [],
    }
    rets = _equity_to_returns(equity_curve)
    if rets is None:
        result["notes"].append("insufficient_equity_curve")
        persist_latest(result)
        return result

    engine = "json_stub"
    if not quantstats_enabled():
        result["notes"].append("LAB_QUANTSTATS=0 — stub only")
    else:
        try:
            import quantstats as qs

            qs.extend_pandas()
            out_html.parent.mkdir(parents=True, exist_ok=True)
            if spy_returns is not None and len(spy_returns) > 5:
                aligned = pd.concat([rets, spy_returns.rename("spy")], axis=1, join="inner").dropna()
                if len(aligned) >= 5:
                    qs.reports.html(
                        aligned.iloc[:, 0],
                        benchmark=aligned["spy"],
                        output=str(out_html),
                        title=title,
                    )
                else:
                    qs.reports.html(rets, output=str(out_html), title=title)
            else:
                qs.reports.html(rets, output=str(out_html), title=title)
            engine = "quantstats"
        except Exception as exc:
            logger.info("QuantStats tear failed: %s", exc)
            result["notes"].append(str(exc))
            engine = "json_stub_after_error"

    summary = summary_metrics(rets, spy_returns, engine=engine)
    stub = _stub_path()
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    html_ok = engine == "quantstats" and out_html.exists() and out_html.stat().st_size > 100
    result.update(
        {
            "available": True,
            "engine": engine,
            "complete": True,
            "path": str(out_html if html_ok else stub),
            "stub_path": str(stub),
            "html_available": html_ok,
            "summary": summary,
        }
    )
    persist_latest(result)
    return result


def build_desk_tear(
    *,
    ticker: Optional[str] = None,
    period: str = "1y",
    spy_returns: Optional[pd.Series] = None,
    close: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    """Desk entry: paper book when ticker is empty, else one-name vs SPY stub/HTML."""
    notes: List[str] = []
    sym = (ticker or "").upper().strip() or None
    if sym:
        vendor = "injected"
        series = close
        if series is None:
            series, vendor, fetch_notes = load_close_series(sym, period=period)
            notes.extend(fetch_notes)
        curve = series_to_curve(series) if series is not None else []
        if spy_returns is None:
            try:
                spy_returns = load_spy_returns(series.index if series is not None else None)
            except Exception as exc:
                notes.append(f"spy:{exc}")
        result = build_tear_sheet(
            equity_curve=curve,
            spy_returns=spy_returns,
            title=f"Cgab {sym}",
            out_html=_html_path("ticker", sym),
            scope="ticker",
            ticker=sym,
            vendor=vendor,
        )
        result["notes"] = list(result.get("notes") or []) + notes
        persist_latest(result)
        return result

    curve, paper_notes = load_paper_equity_curve()
    notes.extend(paper_notes)
    if spy_returns is None:
        try:
            spy_returns = load_spy_returns()
        except Exception as exc:
            notes.append(f"spy:{exc}")
    result = build_tear_sheet(
        equity_curve=curve,
        spy_returns=spy_returns,
        title="Cgab paper book",
        scope="paper",
        vendor="paper_ledger",
    )
    result["notes"] = list(result.get("notes") or []) + notes
    persist_latest(result)
    return result
