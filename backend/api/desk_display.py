"""Desk honesty-display helpers (Slice D D4–D6).

Labels must match evidence. Do not invent product meaning:
account loaded ≠ worker running; local-store book ≠ broker book;
paper KPIs ≠ backtest KPIs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


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
    return {
        "orders": list(rows or []),
        "count": len(rows or []),
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
