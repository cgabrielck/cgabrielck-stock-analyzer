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


# --- Wave 1 daily digest (filled vs pending; skips; paper vs SPY net of costs) ---

_ET_NAME = "America/New_York"
PENDING_STATUSES = {
    "new",
    "accepted",
    "pending_new",
    "held",
    "partially_filled",
    "submitted",
    "open",
    "pending",
    "draft",
    "risk_approved",
}
FILLED_STATUSES = {"filled"}


def _status_key(order: Dict[str, Any]) -> str:
    raw = order.get("status")
    if hasattr(raw, "value"):
        raw = raw.value
    text = str(raw or "").strip().lower()
    if text.startswith("orderstatus."):
        text = text.split(".", 1)[-1]
    return text


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _on_et_date(value: Any, day) -> bool:
    dt = _parse_dt(value)
    if dt is None or day is None:
        return False
    try:
        from zoneinfo import ZoneInfo

        et = ZoneInfo(_ET_NAME)
    except Exception:
        et = timezone.utc
    return dt.astimezone(et).date() == day


def _order_brief(order: Dict[str, Any]) -> Dict[str, Any]:
    qty = order.get("quantity") or order.get("qty") or 0
    try:
        qty_f = float(qty)
    except (TypeError, ValueError):
        qty_f = 0.0
    px = order.get("limit_price") or order.get("filled_avg_price")
    try:
        px_f = float(px) if px not in (None, "") else None
    except (TypeError, ValueError):
        px_f = None
    return {
        "symbol": str(order.get("symbol") or "").upper(),
        "status": _status_key(order),
        "qty": qty_f,
        "limit_price": px_f,
        "filled_avg_price": order.get("filled_avg_price"),
        "filled_at": str(order.get("filled_at") or "")[:19] or None,
        "submitted_at": str(order.get("submitted_at") or order.get("created_at") or "")[:19] or None,
        "side": str(order.get("side") or "").lower(),
    }


def _benchmark_from_paper(paper: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    paper = paper or {}
    bench = paper.get("benchmark") if isinstance(paper.get("benchmark"), dict) else {}
    costs = paper.get("costs") if isinstance(paper.get("costs"), dict) else {}
    pnl = paper.get("pnl") if isinstance(paper.get("pnl"), dict) else {}
    empty = bool(paper.get("empty_book"))
    alpha_net = bench.get("alpha_net_of_costs_pct")
    if alpha_net is None:
        alpha_net = costs.get("alpha_net_of_costs_pct")
    spy = bench.get("spy_return_pct")
    beats = bench.get("beats_spy_net")
    if beats is None and not empty:
        beats = paper.get("beats_spy_net")
    cost_bps = bench.get("assumed_cost_bps")
    if cost_bps is None:
        cost_bps = costs.get("assumed_round_trip_bps", DEFAULT_COST_BPS)
    return {
        "alpha_net_of_costs_pct": None if empty else alpha_net,
        "spy_return_pct": None if empty else spy,
        "beats_spy_net": None if empty else beats,
        "assumed_cost_bps": cost_bps,
        "day_pnl": pnl.get("day_pnl"),
        "equity": pnl.get("final_equity") or pnl.get("alpaca_equity"),
        "empty_book": empty,
        "generated_at": paper.get("generated_at"),
    }


def build_daily_digest(
    *,
    paper: Optional[Dict[str, Any]] = None,
    persist_paper: bool = False,
    as_of_day: Optional[Any] = None,
    orders: Optional[List[Dict[str, Any]]] = None,
    worker_status: Optional[Dict[str, Any]] = None,
    scan_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Filled vs pending + skip summary + paper vs SPY net of costs.

    Unfilled ``new`` / ``accepted`` limits are listed as pending and are **not** a seal.
    """
    from zoneinfo import ZoneInfo

    et = ZoneInfo(_ET_NAME)
    day = as_of_day or datetime.now(et).date()

    orders = list(orders if orders is not None else _load_paper_orders())
    filled_today: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    for row in orders:
        brief = _order_brief(row)
        if not brief["symbol"]:
            continue
        status = brief["status"]
        if status in FILLED_STATUSES and _on_et_date(
            row.get("filled_at") or row.get("updated_at") or row.get("created_at"), day
        ):
            filled_today.append(brief)
        elif status in PENDING_STATUSES:
            pending.append(brief)

    pending_symbols = [p["symbol"] for p in pending if p["symbol"]]
    skip_counts: Dict[str, Any] = {}
    strategy = None
    st = worker_status
    if st is None:
        try:
            from backend.api import worker_ctl

            st = worker_ctl.status()
        except Exception as exc:
            logger.debug("digest worker status skipped: %s", exc)
            st = {}
    if not isinstance(st, dict):
        st = {}
    skip_counts = st.get("skip_counts") if isinstance(st.get("skip_counts"), dict) else {}
    strategy = st.get("strategy")
    for symbol in st.get("pending_buys") or []:
        token = str(symbol or "").upper()
        if token and token not in pending_symbols:
            pending.append({"symbol": token, "status": "new", "qty": None, "source": "heartbeat"})
            pending_symbols.append(token)

    scan: Dict[str, Any] = {}
    raw = scan_payload
    if raw is None:
        try:
            from backend.api.research_jobs import latest_scan

            raw = latest_scan()
        except Exception as exc:
            logger.debug("digest scan skipped: %s", exc)
            raw = {}
    try:
        from backend.api.research_jobs import signal_max_age_hours as _sig_age

        max_age = raw.get("signal_max_age_hours") or _sig_age()
    except Exception:
        max_age = raw.get("signal_max_age_hours")
    stale = bool(raw.get("stale") or not raw.get("available"))
    scan = {
        "as_of": raw.get("ts") or raw.get("scan_as_of") or raw.get("as_of"),
        "stale": stale,
        "available": bool(raw.get("available")),
        "top5": raw.get("top5_tickers") or raw.get("top5") or [],
        "signal_max_age_hours": max_age,
        "fresh_label_zh": "過期" if stale else "新鮮",
        "fresh_label_en": "stale" if stale else "fresh",
    }

    if paper is None:
        paper = load_cached_paper_report()
        if persist_paper or paper is None:
            try:
                paper = build_paper_report(persist=persist_paper)
            except Exception as exc:
                logger.debug("digest paper report skipped: %s", exc)
                paper = paper or {}

    bench = _benchmark_from_paper(paper)
    day_pnl = bench.get("day_pnl")
    equity = bench.get("equity")
    try:
        from backend.trading.alpaca_broker import AlpacaBroker

        summary = AlpacaBroker().get_account_summary()
        if summary.day_pnl is not None:
            day_pnl = summary.day_pnl
        equity = summary.equity or summary.portfolio_value or equity
    except Exception as exc:
        logger.debug("digest alpaca skipped: %s", exc)

    from backend.trading.strategies.skip_codes import format_skip_lines

    skip_zh = format_skip_lines(skip_counts, lang="zh") or "—"
    skip_en = format_skip_lines(skip_counts, lang="en") or "—"
    filled_syms = [r["symbol"] for r in filled_today]
    pending_syms = [r["symbol"] for r in pending]
    disclaimer_zh = (
        "未成交限價單（new／accepted）不算封印。filled 才入帳。"
        " paper vs SPY 已扣假設來回成本。"
    )
    disclaimer_en = (
        "Unfilled limits (new/accepted) are not a seal. Only filled shares count. "
        "Paper vs SPY is net of assumed round-trip costs."
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": str(day),
        "timezone": _ET_NAME,
        "strategy": strategy,
        "filled": {
            "count": len(filled_today),
            "symbols": filled_syms,
            "orders": filled_today[:20],
        },
        "pending": {
            "count": len(pending),
            "symbols": pending_syms,
            "orders": pending[:20],
            "not_a_seal": True,
        },
        "skips": {
            "counts": skip_counts,
            "summary_zh": skip_zh,
            "summary_en": skip_en,
        },
        "benchmark": {
            **bench,
            "day_pnl": day_pnl,
            "equity": equity,
        },
        "scan": scan,
        "disclaimer_zh": disclaimer_zh,
        "disclaimer_en": disclaimer_en,
        "not_a_seal": True,
        "available": True,
        "places_order": False,
        "session_date_et": str(day),
        "book": {
            "filled_today": filled_syms,
            "filled_today_count": len(filled_today),
            "pending": pending_syms,
            "pending_count": len(pending),
            "note": "pending = new/accepted/working at broker; not a fill and not a seal.",
        },
        "pnl": {
            "day_pnl": day_pnl,
            "equity": equity,
            "alpha_net_of_costs_pct": bench.get("alpha_net_of_costs_pct"),
            "beats_spy_net": bench.get("beats_spy_net"),
        },
        "worker": {
            "strategy": strategy,
            "running": bool(st.get("running")) if isinstance(st, dict) else None,
            "pending_buys": list(st.get("pending_buys") or []) if isinstance(st, dict) else pending_syms,
        },
    }
    payload["text_zh"] = format_digest_text(payload)
    payload["text_html"] = format_digest_html(payload)
    return payload


def _fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def format_digest_text(digest: Dict[str, Any], *, heading: str = "📋 日報 Daily digest") -> str:
    filled = digest.get("filled") or {}
    pending = digest.get("pending") or {}
    skips = digest.get("skips") or {}
    bench = digest.get("benchmark") or {}
    scan = digest.get("scan") or {}
    as_of = str(scan.get("as_of") or "—").replace("T", " ")[:19]
    stale = "過期" if scan.get("stale") or not scan.get("available") else "新鮮"
    filled_list = ", ".join(filled.get("symbols") or []) or "—"
    pending_list = ", ".join(pending.get("symbols") or []) or "—"
    top5 = ", ".join((scan.get("top5") or [])[:5]) or "—"
    lines = [
        heading,
        f"日期 {digest.get('as_of_date') or '—'}（美東）",
        f"掃描：{stale} · as-of {as_of} · Top5 {top5}",
        f"今日成交 filled：{int(filled.get('count') or 0)} · {filled_list}",
        f"排隊 new／pending：{int(pending.get('count') or 0)} · {pending_list}",
        "未成交限價單不算封印。",
        f"為何沒買：{skips.get('summary_zh') or '—'}",
        f"策略：{digest.get('strategy') or '—'}",
    ]
    if bench.get("day_pnl") is not None:
        lines.append(f"Day P&L：{_fmt_money(bench.get('day_pnl'))}")
    if bench.get("equity") is not None:
        lines.append(f"Equity：{_fmt_money(bench.get('equity'))}")
    if bench.get("alpha_net_of_costs_pct") is not None:
        spy = bench.get("spy_return_pct")
        spy_s = f" · SPY {_fmt_pct(spy)}" if spy is not None else ""
        beats = bench.get("beats_spy_net")
        beats_s = " · 扣成本贏 SPY" if beats is True else (" · 扣成本未贏 SPY" if beats is False else "")
        lines.append(f"vs SPY（扣成本）：{_fmt_pct(bench.get('alpha_net_of_costs_pct'))}{spy_s}{beats_s}")
    else:
        lines.append("vs SPY（扣成本）：尚無成交帳本")
    lines.append("紙上模擬 · 非正式投資建議")
    return "\n".join(lines)


def format_digest_html(digest: Dict[str, Any], *, heading: Optional[str] = None) -> str:
    import html as _html

    def esc(value: Any) -> str:
        return _html.escape(str(value), quote=False)

    filled = digest.get("filled") or {}
    pending = digest.get("pending") or {}
    skips = digest.get("skips") or {}
    bench = digest.get("benchmark") or {}
    scan = digest.get("scan") or {}
    as_of = str(scan.get("as_of") or "—").replace("T", " ")[:19]
    stale = "過期" if scan.get("stale") or not scan.get("available") else "新鮮"
    filled_list = ", ".join(filled.get("symbols") or []) or "—"
    pending_list = ", ".join(pending.get("symbols") or []) or "—"
    top5 = ", ".join((scan.get("top5") or [])[:5]) or "—"
    title = heading or "📋 <b>日報 Daily digest</b>"
    lines = [
        title,
        "",
        f"<b>日期:</b> {esc(digest.get('as_of_date') or '—')}（美東）",
        f"<b>掃描:</b> {esc(stale)} · as-of {esc(as_of)}",
        f"<b>Top5:</b> {esc(top5)}",
        f"<b>今日成交 filled:</b> {int(filled.get('count') or 0)} · {esc(filled_list)}",
        f"<b>排隊 new／pending:</b> {int(pending.get('count') or 0)} · {esc(pending_list)}",
        "<b>未成交限價單不算封印。</b>",
        f"<b>為何沒買:</b> {esc(skips.get('summary_zh') or '—')}",
        f"<b>策略:</b> {esc(digest.get('strategy') or '—')}",
    ]
    if bench.get("day_pnl") is not None:
        lines.append(f"<b>Day P&amp;L:</b> {esc(_fmt_money(bench.get('day_pnl')))}")
    if bench.get("equity") is not None:
        lines.append(f"<b>Equity:</b> {esc(_fmt_money(bench.get('equity')))}")
    if bench.get("alpha_net_of_costs_pct") is not None:
        spy = bench.get("spy_return_pct")
        extra = f" · SPY {esc(_fmt_pct(spy))}" if spy is not None else ""
        beats = bench.get("beats_spy_net")
        if beats is True:
            extra += " · 扣成本贏 SPY"
        elif beats is False:
            extra += " · 扣成本未贏 SPY"
        lines.append(f"<b>vs SPY (net cost):</b> {esc(_fmt_pct(bench.get('alpha_net_of_costs_pct')))}{extra}")
    else:
        lines.append("<b>vs SPY (net cost):</b> 尚無成交帳本")
    lines.append("")
    lines.append("紙上模擬 · 非正式投資建議")
    return "\n".join(lines)


def digest_status_line(digest: Optional[Dict[str, Any]] = None) -> str:
    payload = digest or build_daily_digest()
    filled_n = int((payload.get("filled") or {}).get("count") or 0)
    pending_n = int((payload.get("pending") or {}).get("count") or 0)
    return (
        f"今日成交 {filled_n} · 排隊 {pending_n}（未成交不算封印）。點「日報」看完整摘要。"
    )
