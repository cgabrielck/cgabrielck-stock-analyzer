"""Desk honesty-display helpers (Slice D D4–D7).

Labels must match evidence. Do not invent product meaning:
account loaded ≠ worker running; local-store book ≠ broker book;
paper KPIs ≠ backtest KPIs; Kelly/lab ``data/`` ≠ Desk broker ``backend/data/``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.trading.fill_honesty import honest_order_row, recorded_fill_price
from backend.trading.risk.gates import positive_price

UNPRICED_FILL_LABEL = "unpriced — not a fill"
KPI_EMPTY_CUE = "empty paper book — not backtest"
LOCAL_ORDER_CUE = "local/unverified"
# D7: product-facing relative labels. Not live/verified stamps.
LAB_BOOK_DIR_LABEL = "data/"
BROKER_BOOK_DIR_LABEL = "backend/data/"
DUAL_BOOK_CUE = (
    "Two books, two directories. Desk broker journal/ledger is backend/data/. "
    "Streamlit Kelly/lab portfolio is data/. They are not the same book."
)
DUAL_BOOK_CUE_ZH = (
    "兩本帳、兩個目錄。Desk 券商日誌／帳本在 backend/data/。"
    "Streamlit Kelly／實驗室組合在 data/。兩者不是同一本帳。"
)
DUAL_BOOK_UNIFIED_CUE = (
    "Lab Kelly and Desk broker paths currently resolve to the same directory. "
    "This is not a live or verified stamp."
)
DUAL_BOOK_UNIFIED_CUE_ZH = (
    "Kelly／實驗室與 Desk 券商路徑目前指向同一目錄。這不是 live 或 verified 標記。"
)
_JOURNAL_FILL_STATUSES = frozenset({"filled", "partially_filled"})
_FILL_ITEM_PRICE_KEYS = ("price", "filled_avg_price", "fill_price", "filled_price")
_DEAD_PROCESS_EVIDENCE = frozenset({"", "none", "pid_dead", "heartbeat_unverified"})
_OPEN_ORDER_STATUSES = frozenset(
    {
        "draft",
        "risk_approved",
        "submitted",
        "accepted",
        "partially_filled",
        "acceptance_unknown",
        "new",
        "pending_new",
        "pending_cancel",
        "pending_replace",
        "held",
    }
)


def lab_book_dir() -> Path:
    """Repo-root ``data/`` — Streamlit Kelly / ``portfolio_manager`` book.

    Matches ``backend/agents/portfolio_manager.py`` (``dirname(__file__)/../.. /data``).
    Do not import that module here (heavy research deps). Fail-cases compare both.
    """
    return (Path(__file__).resolve().parents[2] / "data").resolve()


def broker_book_dir() -> Path:
    """``backend/data/`` — Desk journal / ledger / worker (``constants.DATA_DIR``)."""
    from backend.utils.constants import DATA_DIR

    return Path(DATA_DIR).resolve()


def dual_books_split() -> bool:
    """True while lab Kelly and Desk broker still use different directories."""
    return lab_book_dir() != broker_book_dir()


def dual_book_envelope() -> Dict[str, Any]:
    """D7-HONESTY envelope. ``design_open`` stays true while dirs remain split.

    Never stamps verified/live. Dual dirs are an intentional lab vs broker split
    (D7-DESIGN); this payload only labels them.
    """
    split = dual_books_split()
    return {
        "dual_books": split,
        "design_open": split,
        "lab_book_dir": LAB_BOOK_DIR_LABEL,
        "broker_book_dir": BROKER_BOOK_DIR_LABEL,
        "disclaimer": DUAL_BOOK_CUE if split else DUAL_BOOK_UNIFIED_CUE,
        "disclaimer_zh": DUAL_BOOK_CUE_ZH if split else DUAL_BOOK_UNIFIED_CUE_ZH,
        "verified": False,
        "live": False,
    }


def worker_process_on(status: Optional[Dict[str, Any]] = None) -> bool:
    """D4: AUTO / Worker chip ON only with live pid evidence that is not stale.

    ``worker_ctl.status`` may still report ``running=True`` for an alive-but-stale
    pid (process exists, heartbeat expired). The chip must not read as linked/ON.
    """
    status = status or {}
    unverified = bool(
        status.get("unverified") or status.get("process_evidence") == "heartbeat_unverified"
    )
    if unverified or status.get("stale"):
        return False
    evidence = str(status.get("process_evidence") or "").strip()
    if evidence in _DEAD_PROCESS_EVIDENCE:
        return False
    alive = bool(status.get("alive")) or evidence == "pid_alive"
    return bool(status.get("running")) and alive


def display_chips(status: Optional[Dict[str, Any]] = None, *, account_loaded: bool = False) -> Dict[str, Any]:
    """D4: account presence and process evidence are separate chips.

    Never treat account-loaded, stale heartbeat, missing pid, or
    heartbeat_unverified as a green / linked-as-running signal.
    """
    status = status or {}
    unverified = bool(
        status.get("unverified") or status.get("process_evidence") == "heartbeat_unverified"
    )
    stale = bool(status.get("stale"))
    on = worker_process_on(status)
    if unverified:
        worker_label = "Worker unverified"
        worker_tone = "warn"
    elif stale:
        worker_label = "Worker stale"
        worker_tone = "warn"
    elif on:
        worker_label = "Worker running"
        worker_tone = "on"
    else:
        worker_label = "Worker not running"
        worker_tone = "off"
    return {
        "account_loaded": bool(account_loaded),
        "account_label": "Account loaded" if account_loaded else "Account —",
        "worker_running": on,
        "worker_label": worker_label,
        "worker_tone": worker_tone,
        "auto_on": on,
        "verified": False,
        "live": False,
    }


def is_open_order_status(status: Any) -> bool:
    return str(status or "").strip().lower() in _OPEN_ORDER_STATUSES


def order_display_status(row: Optional[Dict[str, Any]]) -> str:
    """Row status as shown on Desk. Local fallback must not read as a live broker fill."""
    row = row or {}
    st = str(row.get("status") or "—") or "—"
    local = bool(row.get("degraded") or row.get("unverified") or row.get("source") == "local_store")
    if local:
        return LOCAL_ORDER_CUE if st in {"", "—"} else f"{st} · {LOCAL_ORDER_CUE}"
    return st


def _stamp_order_row(
    row: Dict[str, Any],
    *,
    source: str,
    degraded: bool,
    unverified: bool,
) -> Dict[str, Any]:
    stamped = dict(row)
    stamped["source"] = source
    stamped["degraded"] = bool(degraded)
    stamped["unverified"] = bool(unverified)
    stamped["verified"] = False
    stamped["live"] = False
    stamped["display_status"] = order_display_status(stamped)
    return stamped


def orders_book(
    rows: List[Dict[str, Any]],
    *,
    source: str,
    degraded: bool,
    unverified: bool,
    status: str = "all",
) -> Dict[str, Any]:
    """D5: GET /api/orders envelope. Fallback must not look like a clean broker book.

    ``status=open`` filters **local_store** rows only. A successful broker book
    is returned as Alpaca sent it (including still-active statuses such as
    ``done_for_day``).
    """
    src = str(source or "unknown")
    local = src == "local_store"
    degraded_flag = bool(degraded) or local
    unverified_flag = bool(unverified) or local
    honest_rows = [honest_order_row(row) for row in (rows or [])]
    # Open filter is local-store only. Broker OPEN already comes from Alpaca;
    # re-allowlisting would drop still-active statuses such as done_for_day.
    if local and str(status or "all").strip().lower() == "open":
        honest_rows = [row for row in honest_rows if is_open_order_status(row.get("status"))]
    stamped = [
        _stamp_order_row(row, source=src, degraded=degraded_flag, unverified=unverified_flag)
        for row in honest_rows
    ]
    return {
        "orders": stamped,
        "count": len(stamped),
        "source": src,
        "degraded": degraded_flag,
        "unverified": unverified_flag,
        "verified": False,
        "live": False,
    }


def desk_kpi_source_label(*, kpi_source: Optional[str] = None, empty_book: bool = False) -> str:
    """Desk KPI parenthetical. Empty paper must not read as backtest."""
    if empty_book or kpi_source in (None, "", "none"):
        return "empty"
    src = str(kpi_source)
    if src in {"paper", "backtest"}:
        return src
    return "—"


def compose_performance_kpis(
    paper: Optional[Dict[str, Any]],
    summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """D6: top-level KPI fields carry an honest source.

    Paper fills drive top-level fields labeled paper. Empty paper book does
    **not** copy stable backtest numbers into those fields — backtest stays
    under ``backtest.*`` for the Strategies page. ``summary`` is accepted so
    callers keep passing the nested backtest blob; it is not copied upward.
    """
    _ = summary
    empty = bool(paper.get("empty_book")) if paper else True
    use_paper = bool(paper) and not empty
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
        cue = None
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
        cue = KPI_EMPTY_CUE
    return {
        "kpi_source": kpi_source,
        "empty_book": empty,
        "kpi_cue": cue,
        "kpi_label": desk_kpi_source_label(kpi_source=kpi_source, empty_book=empty),
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


def _claims_fill_status(row: Optional[Dict[str, Any]]) -> bool:
    st = str((row or {}).get("status") or "").lower()
    return st in _JOURNAL_FILL_STATUSES or st == UNPRICED_FILL_LABEL.lower()


def journal_status_label(row: Optional[Dict[str, Any]]) -> str:
    row = row or {}
    st = str(row.get("status") or "").lower()
    if st == UNPRICED_FILL_LABEL.lower():
        return UNPRICED_FILL_LABEL
    if st in _JOURNAL_FILL_STATUSES and not is_priced_fill(row):
        return UNPRICED_FILL_LABEL
    return str(row.get("status") or "—")


def journal_status_class(row: Optional[Dict[str, Any]]) -> str:
    """CSS token after ``jnl-status``. Unpriced fills must not be mint ``filled``."""
    row = row or {}
    st = str(row.get("status") or "").lower()
    if st == UNPRICED_FILL_LABEL.lower() or (
        st in _JOURNAL_FILL_STATUSES and not is_priced_fill(row)
    ):
        return "submitted"
    return st


def journal_summary_label(row: Optional[Dict[str, Any]]) -> str:
    """Do not leave summary saying filled while status shows unpriced."""
    row = row or {}
    summary = str(row.get("user_visible_summary") or row.get("note") or "").strip()
    st = str(row.get("status") or "").lower()
    claiming = st in _JOURNAL_FILL_STATUSES or st == UNPRICED_FILL_LABEL.lower()
    if not claiming or is_priced_fill(row):
        return summary
    if not summary:
        return UNPRICED_FILL_LABEL
    low = summary.lower()
    if "filled" in low:
        return UNPRICED_FILL_LABEL
    if "unpriced" in low:
        return summary
    return f"{summary} · {UNPRICED_FILL_LABEL}"


def journal_display_row(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Display overlay for list + entry JSON. Does not write the fill ledger."""
    data = dict(row or {})
    priced = is_priced_fill(data)
    st = str(data.get("status") or "").lower()
    claiming = _claims_fill_status(data)
    if claiming and not priced:
        src = dict(data)
        if st == UNPRICED_FILL_LABEL.lower():
            src["status"] = "filled"
        data["display_status"] = UNPRICED_FILL_LABEL
        data["display_status_class"] = "submitted"
        data["user_visible_summary"] = journal_summary_label(src)
        data["kind"] = "order"
        data["status"] = UNPRICED_FILL_LABEL
        data["fills"] = []
        return data
    data["display_status"] = journal_status_label(data)
    data["display_status_class"] = journal_status_class(data)
    data["user_visible_summary"] = journal_summary_label(data)
    return data


def _demote_fill_status_fields(obj: Dict[str, Any]) -> None:
    st = str(obj.get("status") or "").lower()
    if st in _JOURNAL_FILL_STATUSES:
        obj["status"] = UNPRICED_FILL_LABEL


def journal_display_audit(
    audit: Optional[Dict[str, Any]],
    *,
    entry: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Display overlay for Entry+audit JSON. Does not write audit jsonl.

    Demote when the journal row has no fill price > 0 and the attached
    audit/payload claims filled / partially_filled without a real fill
    price. ``limit_price`` is not a fill. Do not require the entry status
    itself to already read unpriced / filled (from_order and audit-jsonl
    rows often normalize to submitted / proposed first).
    """
    if not isinstance(audit, dict):
        return audit
    data = dict(audit)
    payload = dict(data["payload"]) if isinstance(data.get("payload"), dict) else None
    if payload is not None:
        data["payload"] = payload
    if is_priced_fill(entry):
        return data
    if is_priced_fill(data) or (payload is not None and is_priced_fill(payload)):
        return data
    _demote_fill_status_fields(data)
    if payload is not None:
        _demote_fill_status_fields(payload)
    return data
