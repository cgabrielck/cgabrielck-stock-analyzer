"""Paper book performance vs SPY for the FastAPI desk (thin Slice B)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.trading.performance.tracker import (
    INITIAL_CAPITAL,
    evaluate_performance,
    persist_performance_snapshot,
    SealCriteria,
    seal_stage2,
)
from backend.trading.storage import create_order_store
from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

# Stated round-trip cost assumption (bps) for excess-vs-SPY honesty.
DEFAULT_COST_BPS = 10.0  # 0.10% round-trip per turn


def _load_paper_orders() -> List[Dict[str, Any]]:
    store = None
    try:
        store = create_order_store()
        if hasattr(store, "export_dicts"):
            rows = store.export_dicts()
            # Normalize side enums / status for tracker
            out: List[Dict[str, Any]] = []
            for row in rows:
                side = row.get("side")
                if hasattr(side, "value"):
                    side = side.value
                status = row.get("status")
                if hasattr(status, "value"):
                    status = status.value
                out.append(
                    {
                        **row,
                        "side": str(side or "").lower(),
                        "status": str(status or "").lower(),
                        "quantity": float(row.get("quantity") or row.get("qty") or 0),
                        "filled_avg_price": row.get("filled_avg_price") or row.get("limit_price"),
                        "filled_at": row.get("filled_at") or row.get("updated_at") or row.get("created_at"),
                    }
                )
            return out
    except Exception as exc:
        logger.debug("paper order export failed: %s", exc)
    finally:
        if store is not None and hasattr(store, "close"):
            try:
                store.close()
            except Exception:
                pass

    # Fallback JSON ledger
    for name in ("trading_orders.json", "shadow_orders.json"):
        path = Path(DATA_DIR) / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("orders", [])
            return list(data or [])
        except Exception:
            continue
    return []


def _turnover_pct(orders: List[Dict[str, Any]], equity: float) -> float:
    if equity <= 0:
        return 0.0
    notional = 0.0
    for o in orders:
        if str(o.get("status") or "").lower() != "filled":
            continue
        qty = float(o.get("quantity") or 0)
        px = float(o.get("filled_avg_price") or o.get("limit_price") or 0)
        notional += abs(qty * px)
    return round(100.0 * notional / equity, 2)


def build_paper_report(
    *,
    initial_capital: Optional[float] = None,
    cost_bps: float = DEFAULT_COST_BPS,
    persist: bool = True,
) -> Dict[str, Any]:
    """Evaluate paper ledger vs SPY; optionally write performance_paper.json."""
    orders = _load_paper_orders()
    capital = float(initial_capital or INITIAL_CAPITAL)

    # Prefer Alpaca equity as starting capital when we have no fills yet
    alpaca_equity = None
    day_pnl = None
    try:
        from backend.trading.alpaca_broker import AlpacaBroker

        summary = AlpacaBroker().get_account_summary()
        alpaca_equity = float(summary.equity or summary.portfolio_value or 0) or None
        day_pnl = summary.day_pnl
        if alpaca_equity and not orders:
            capital = alpaca_equity
    except Exception as exc:
        logger.debug("alpaca equity for paper report skipped: %s", exc)

    # Write temp orders path for evaluate_performance
    tmp_path = Path(DATA_DIR) / "_paper_orders_eval.json"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(json.dumps(orders, indent=2, default=str), encoding="utf-8")

    try:
        report = evaluate_performance(orders_path=tmp_path, initial_capital=capital)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    # Cost-adjusted excess: subtract cost_bps * turnover_turns from alpha
    turnover = _turnover_pct(orders, report.final_equity or capital or 1.0)
    # Approximate turns as turnover/100 (one full book = 100% turnover)
    cost_drag_pct = (cost_bps / 10000.0) * (turnover / 100.0) * 100.0  # percent points
    alpha_gross = report.alpha_pct
    alpha_net = alpha_gross - cost_drag_pct

    seal = seal_stage2(report, SealCriteria())
    if persist and report.trading_days > 0:
        try:
            persist_performance_snapshot(
                report, seal, path=Path(DATA_DIR) / "performance_paper.json", mode="paper"
            )
            # Enrich file with cost assumptions
            path = Path(DATA_DIR) / "performance_paper.json"
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["costs"] = {
                    "assumed_round_trip_bps": cost_bps,
                    "turnover_pct": turnover,
                    "cost_drag_pct": round(cost_drag_pct, 4),
                    "alpha_gross_pct": round(alpha_gross, 4),
                    "alpha_net_of_costs_pct": round(alpha_net, 4),
                }
                payload["beats_spy_net"] = bool(alpha_net > 0)
                path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            logger.warning("persist performance_paper failed: %s", exc)

    empty = report.trading_days == 0 and report.num_trades == 0
    alpha_gross_out = None if empty else round(alpha_gross, 4)
    alpha_net_out = None if empty else round(alpha_net, 4)
    return {
        "available": not empty or alpaca_equity is not None,
        "empty_book": empty,
        "source": "sqlite_orders" if orders else "alpaca_equity_only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {
            "start": report.start_date.isoformat() if report.start_date else None,
            "end": report.end_date.isoformat() if report.end_date else None,
            "trading_days": report.trading_days,
        },
        "pnl": {
            "initial_capital": report.initial_capital,
            "final_equity": report.final_equity or alpaca_equity,
            "total_return_pct": report.total_return_pct if not empty else None,
            "annualized_return_pct": report.annualized_return_pct if not empty else None,
            "day_pnl": day_pnl,
            "alpaca_equity": alpaca_equity,
        },
        "risk": {
            "sharpe_ratio": report.sharpe_ratio if not empty else None,
            "sortino_ratio": report.sortino_ratio if not empty else None,
            "max_drawdown_pct": report.max_drawdown_pct if not empty else None,
            "volatility_pct": report.volatility_pct if not empty else None,
        },
        "trading": {
            "num_trades": report.num_trades,
            "win_rate_pct": report.win_rate_pct if not empty else None,
            "profit_factor": report.profit_factor if not empty else None,
            "turnover_pct": turnover if not empty else 0.0,
        },
        "benchmark": {
            "spy_return_pct": report.spy_return_pct if not empty else None,
            "alpha_gross_pct": alpha_gross_out,
            "alpha_net_of_costs_pct": alpha_net_out,
            "beta": report.beta if not empty else None,
            "assumed_cost_bps": cost_bps,
            "cost_drag_pct": round(cost_drag_pct, 4) if not empty else None,
            "beats_spy_net": bool(alpha_net > 0) if not empty else None,
        },
        "seal_preview": {
            "passed": seal.passed if not empty else False,
            "reasons": seal.reasons if not empty else ["empty_book"],
        },
        "disclaimer": (
            "Paper metrics are not live capital. Excess vs SPY subtracts a stated "
            f"{cost_bps:g} bps round-trip cost × turnover. If alpha_net ≤ 0, sell "
            "SKU B as risk-gated research-list automation — not alpha."
        ),
    }


def load_cached_paper_report() -> Optional[Dict[str, Any]]:
    path = Path(DATA_DIR) / "performance_paper.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
