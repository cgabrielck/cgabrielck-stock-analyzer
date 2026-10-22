"""Desk honesty-display helpers (Slice D D4–D6).

Labels must match evidence. Do not invent product meaning:
account loaded ≠ worker running; local-store book ≠ broker book;
paper KPIs ≠ backtest KPIs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.trading.fill_honesty import honest_order_row, recorded_fill_price
from backend.trading.risk.gates import positive_price

UNPRICED_FILL_LABEL = "unpriced — not a fill"
_JOURNAL_FILL_STATUSES = frozenset({"filled", "partially_filled"})
_FILL_ITEM_PRICE_KEYS = ("price", "filled_avg_price", "fill_price", "filled_price")


def display_chips(status: Optional[Dict[str, Any]] = None, *, account_loaded: bool = False) -> Dict[str, Any]:
    """D4: account presence and process evidence are separate chips.

    Never treat account-loaded or heartbeat_unverified as a green / linked-as-running signal.
    """
    status = status or {}
    unverified = bool(
        status.get("unverified") or status.get("process_evidence") == "heartbeat_unverified"
    )
    running = bool(status.get("running")) and not unverified
    if unverified:
        worker_label = "Worker unverified"
        worker_tone = "warn"
    elif running:
        worker_label = "Worker running"
        worker_tone = "on"
    else:
        worker_label = "Worker not running"
        worker_tone = "off"
    return {
        "account_loaded": bool(account_loaded),
        "account_label": "Account loaded" if account_loaded else "Account —",
        "worker_running": running,
        "worker_label": worker_label,
        "worker_tone": worker_tone,
        "auto_on": running,
        "verified": False,
        "live": False,
    }


def orders_book(
    rows: List[Dict[str, Any]],
    *,
    source: str,
    degraded: bool,
    unverified: bool,
) -> Dict[str, Any]:
    """D5: GET /api/orders envelope. Fallback must not look like a clean broker book."""
    src = str(source or "unknown")
    local = src == "local_store"
    degraded_flag = bool(degraded) or local
    unverified_flag = bool(unverified) or local
    honest_rows = [honest_order_row(row) for row in (rows or [])]
    return {
        "orders": honest_rows,
        "count": len(honest_rows),
        "source": src,
        "degraded": degraded_flag,
        "unverified": unverified_flag,
        "verified": False,
        "live": False,
    }


def compose_performance_kpis(
    paper: Optional[Dict[str, Any]],
    summary: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """D6: top-level KPI fields carry an honest source. Never mix paper under a backtest claim."""
    summary = summary or {}
    empty = bool(paper.get("empty_book")) if paper else True
    use_paper = bool(paper) and not empty
    has_backtest = bool(summary)
    if use_paper:
        kpi_source = "paper"
        trading = paper.get("trading") or {}
        risk = paper.get("risk") or {}
        pnl = paper.get("pnl") or {}
        kpis = {
            "win_rate_pct": trading.get("win_rate_pct"),
            "profit_factor": trading.get("profit_factor"),
            "sharpe_ratio": risk.get("sharpe_ratio"),
            "max_drawdown_pct": risk.get("max_drawdown_pct"),
            "total_return_pct": pnl.get("total_return_pct"),
            "num_trades": trading.get("num_trades"),
        }
    elif has_backtest:
        kpi_source = "backtest"
        kpis = {
            "win_rate_pct": summary.get("win_rate_pct"),
            "profit_factor": summary.get("profit_factor"),
            "sharpe_ratio": summary.get("sharpe_ratio"),
            "max_drawdown_pct": summary.get("max_drawdown_pct"),
            "total_return_pct": summary.get("total_return_pct"),
            "num_trades": summary.get("num_trades"),
        }
    else:
        kpi_source = "none"
        kpis = {
            "win_rate_pct": None,
            "profit_factor": None,
            "sharpe_ratio": None,
            "max_drawdown_pct": None,
            "total_return_pct": None,
            "num_trades": None,
        }
    return {
        "kpi_source": kpi_source,
        **kpis,
        "verified": False,
        "live": False,
    }


def paper_vs_spy_risk(paper: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Paper vs SPY card: only paper.risk.*. Empty book → em-dash, never backtest."""
    paper = paper or {}
    if not paper or paper.get("empty_book"):
        return {"sharpe_ratio": None, "max_drawdown_pct": None}
    risk = paper.get("risk") or {}
    return {
        "sharpe_ratio": risk.get("sharpe_ratio"),
        "max_drawdown_pct": risk.get("max_drawdown_pct"),
    }


def strategy_fallback_row(perf: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Bakeoff-failure fallback. Paper labels use paper fields only; backtest uses backtest.*."""
    perf = perf or {}
    paper = perf.get("paper") or {}
    backtest = perf.get("backtest") or {}
    if perf.get("kpi_source") == "paper" and paper and not paper.get("empty_book"):
        risk = paper.get("risk") or {}
        pnl = paper.get("pnl") or {}
        trading = paper.get("trading") or {}
        bench = paper.get("benchmark") or {}
        return {
            "strategy": "paper snapshot",
            "sizing": "—",
            "return_pct": pnl.get("total_return_pct"),
            "spy_pct": bench.get("spy_return_pct"),
            "alpha_net": bench.get("alpha_net_of_costs_pct"),
            "trades": trading.get("num_trades"),
            "sharpe": risk.get("sharpe_ratio"),
            "max_dd": risk.get("max_drawdown_pct"),
        }
    if backtest.get("source") and backtest.get("source") != "none":
        return {
            "strategy": "stable backtest",
            "sizing": "—",
            "return_pct": backtest.get("total_return_pct"),
            "spy_pct": None,
            "alpha_net": None,
            "trades": backtest.get("num_trades"),
            "sharpe": backtest.get("sharpe_ratio"),
            "max_dd": backtest.get("max_drawdown_pct"),
        }
    return None


def _fill_item_price(item: Any) -> Optional[float]:
    """Price on one fills[] object. Never reads limit_price / entry / mark."""
    if not isinstance(item, dict):
        return None
    for key in _FILL_ITEM_PRICE_KEYS:
        raw = item.get(key)
        if isinstance(raw, bool):
            continue
        px = positive_price(raw)
        if px is not None:
            return px
    return None


def display_fill_price(row: Optional[Dict[str, Any]]) -> Optional[float]:
    """Same contract as the Desk JS ``positiveFillPrice``.

    Top-level recorded fill, else any ``fills[].price`` > 0. Zero, missing,
    and ``limit_price`` are not a fill.
    """
    if not row:
        return None
    px = recorded_fill_price(row)
    if px is not None:
        return px
    for item in row.get("fills") or []:
        nested = _fill_item_price(item)
        if nested is not None:
            return nested
    return None


def is_priced_fill(row: Optional[Dict[str, Any]]) -> bool:
    """Non-empty ``fills`` is not enough. Requires a numeric price > 0."""
    return display_fill_price(row) is not None


def journal_status_label(row: Optional[Dict[str, Any]]) -> str:
    row = row or {}
    st = str(row.get("status") or "").lower()
    if st in _JOURNAL_FILL_STATUSES and not is_priced_fill(row):
        return UNPRICED_FILL_LABEL
    return str(row.get("status") or "—")


def journal_status_class(row: Optional[Dict[str, Any]]) -> str:
    """CSS token after ``jnl-status``. Unpriced fills must not be mint ``filled``."""
    row = row or {}
    st = str(row.get("status") or "").lower()
    if st in _JOURNAL_FILL_STATUSES and not is_priced_fill(row):
        return "submitted"
    return st


def journal_summary_label(row: Optional[Dict[str, Any]]) -> str:
    """Do not leave summary saying filled while status shows unpriced."""
    row = row or {}
    summary = str(row.get("user_visible_summary") or row.get("note") or "").strip()
    st = str(row.get("status") or "").lower()
    if st not in _JOURNAL_FILL_STATUSES or is_priced_fill(row):
        return summary
    if not summary:
        return UNPRICED_FILL_LABEL
    low = summary.lower()
    if "unpriced" in low:
        return summary
    if "filled" in low:
        return UNPRICED_FILL_LABEL
    return f"{summary} · {UNPRICED_FILL_LABEL}"


def journal_display_row(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Display overlay for GET /api/journal. Does not write the fill ledger."""
    data = dict(row or {})
    priced = is_priced_fill(data)
    st = str(data.get("status") or "").lower()
    unpriced_fill = st in _JOURNAL_FILL_STATUSES and not priced
    data["display_status"] = journal_status_label(data)
    data["display_status_class"] = journal_status_class(data)
    data["user_visible_summary"] = journal_summary_label(data)
    if unpriced_fill:
        data["kind"] = "order"
    return data
