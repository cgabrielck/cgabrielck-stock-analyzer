"""US-session auto Scan — FastAPI lifespan thread (not a second process).

Cadence (America/New_York, Mon–Fri):
  - ~09:20 pre-open
  - every SCAN_INTERVAL_MIN during RTH (default 120 → ~09:30, 11:30, …)
  - ~16:10 post-close

Env:
  SCAN_AUTO=1|true
  SCAN_INTERVAL_MIN=120
  SCAN_USE_LLM=true
  SCAN_OUTSIDE_HOURS=false
  SCAN_LANG=zh-TW
  SCAN_LLM_WEIGHT=0.2
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_lock = threading.Lock()
_state: Dict[str, Any] = {
    "enabled": False,
    "running": False,
    "last_trigger_at": None,
    "last_job_id": None,
    "last_error": None,
    "next_scan_at": None,
    "interval_min": 120,
    "use_llm": True,
    "last_digest_at": None,
}
_stop = threading.Event()
_thread: Optional[threading.Thread] = None

PRE_OPEN = dtime(9, 20)
POST_CLOSE = dtime(16, 10)
RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def config() -> Dict[str, Any]:
    interval = max(30, min(360, _env_int("SCAN_INTERVAL_MIN", 120)))
    return {
        "enabled": _env_bool("SCAN_AUTO", False),
        "interval_min": interval,
        "use_llm": _env_bool("SCAN_USE_LLM", True),
        "outside_hours": _env_bool("SCAN_OUTSIDE_HOURS", False),
        "lang": os.getenv("SCAN_LANG", "zh-TW") or "zh-TW",
        "llm_weight": max(0.0, min(0.4, _env_float("SCAN_LLM_WEIGHT", 0.2))),
    }


def _now_et(now: Optional[datetime] = None) -> datetime:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(_ET)


def is_us_weekday(now_et: datetime) -> bool:
    return now_et.weekday() < 5


def in_scan_window(now_et: Optional[datetime] = None, *, outside_hours: bool = False) -> bool:
    """True during pre-open through post-close on US weekdays (or always if outside_hours)."""
    et = now_et or _now_et()
    if outside_hours:
        return True
    if not is_us_weekday(et):
        return False
    t = et.timetz().replace(tzinfo=None)
    return PRE_OPEN <= t <= POST_CLOSE


def _slot_times(interval_min: int) -> List[dtime]:
    """Named session slots: pre-open, RTH every interval from 09:30, post-close."""
    slots: List[dtime] = [PRE_OPEN]
    minutes = RTH_OPEN.hour * 60 + RTH_OPEN.minute
    end = RTH_CLOSE.hour * 60 + RTH_CLOSE.minute
    step = max(30, int(interval_min))
    while minutes <= end:
        slots.append(dtime(minutes // 60, minutes % 60))
        minutes += step
    if POST_CLOSE not in slots:
        slots.append(POST_CLOSE)
    # unique sorted
    uniq = sorted({(s.hour, s.minute) for s in slots})
    return [dtime(h, m) for h, m in uniq]


def next_slot_after(now_et: datetime, interval_min: int, *, outside_hours: bool = False) -> datetime:
    """Next scheduled wall time in ET (may be next weekday)."""
    if outside_hours:
        return now_et + timedelta(minutes=max(30, interval_min))

    slots = _slot_times(interval_min)
    cursor = now_et
    for _ in range(10):  # skip weekends / holidays roughly by weekday
        if is_us_weekday(cursor):
            for slot in slots:
                candidate = cursor.replace(
                    hour=slot.hour, minute=slot.minute, second=0, microsecond=0
                )
                if candidate > now_et:
                    return candidate
        # jump to next calendar day 00:00 then continue
        next_day = (cursor + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cursor = next_day
    return now_et + timedelta(hours=24)


def due_slot(
    now_et: Optional[datetime] = None,
    *,
    interval_min: int = 120,
    outside_hours: bool = False,
    last_trigger_at: Optional[datetime] = None,
) -> Optional[datetime]:
    """Return the slot datetime if we should fire now (within the minute + not already fired)."""
    et = now_et or _now_et()
    if not in_scan_window(et, outside_hours=outside_hours):
        return None

    if outside_hours:
        if last_trigger_at is not None:
            age_min = (et - last_trigger_at.astimezone(_ET)).total_seconds() / 60.0
            if age_min < interval_min:
                return None
        return et.replace(second=0, microsecond=0)

    slots = _slot_times(interval_min)
    t = et.timetz().replace(tzinfo=None)
    matched: Optional[dtime] = None
    for slot in slots:
        slot_dt = et.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)
        # Fire if we are within the first 90s of the slot minute
        delta = (et - slot_dt).total_seconds()
        if 0 <= delta < 90:
            matched = slot
            break
    if matched is None:
        return None

    slot_dt = et.replace(hour=matched.hour, minute=matched.minute, second=0, microsecond=0)
    if last_trigger_at is not None:
        last = last_trigger_at.astimezone(_ET)
        # Same calendar slot already fired
        if last.date() == slot_dt.date() and last.hour == slot_dt.hour and last.minute == slot_dt.minute:
            return None
    return slot_dt


def status() -> Dict[str, Any]:
    cfg = config()
    with _lock:
        snap = dict(_state)
    et = _now_et()
    next_at = snap.get("next_scan_at")
    if cfg["enabled"] and not next_at:
        next_at = next_slot_after(
            et, cfg["interval_min"], outside_hours=cfg["outside_hours"]
        ).isoformat()
    elif isinstance(next_at, datetime):
        next_at = next_at.isoformat()
    last = snap.get("last_trigger_at")
    if isinstance(last, datetime):
        last = last.isoformat()
    return {
        "scan_auto": bool(cfg["enabled"]),
        "scan_auto_running": bool(snap.get("running")),
        "scan_interval_min": cfg["interval_min"],
        "scan_use_llm": cfg["use_llm"],
        "scan_outside_hours": cfg["outside_hours"],
        "next_scan_at": next_at,
        "last_auto_scan_at": last,
        "last_auto_job_id": snap.get("last_job_id"),
        "last_auto_error": snap.get("last_error"),
        "in_scan_window": in_scan_window(et, outside_hours=cfg["outside_hours"]),
    }


def _set_state(**fields: Any) -> None:
    with _lock:
        _state.update(fields)


def _telegram_settings():
    from backend.config import get_telegram_settings

    return get_telegram_settings()


def _maybe_notify(text: str) -> None:
    try:
        from backend.trading.ops_notifier import send_plain

        settings = _telegram_settings()
        if settings and settings.ops_configured:
            send_plain(settings, text)
    except Exception as exc:
        logger.debug("auto-scan notify skipped: %s", exc)


def send_post_close_digest(*, force: bool = False) -> bool:
    """One-screen ops memo after US close: top-5, as-of, skips, day P&L."""
    et = _now_et()
    with _lock:
        last_digest = _state.get("last_digest_at")
    if not force and isinstance(last_digest, datetime):
        last_et = last_digest.astimezone(_ET)
        if last_et.date() == et.date():
            return False

    from backend.api import research_jobs, worker_ctl
    from backend.trading.ops_notifier import send_plain
    from backend.trading.strategies.skip_codes import format_skip_lines

    try:
        settings = _telegram_settings()
    except Exception as exc:
        logger.debug("post-close digest settings failed: %s", exc)
        return False
    if not settings or not settings.ops_configured:
        logger.debug("post-close digest skipped — Telegram not configured")
        return False

    scan = research_jobs.latest_scan()
    st = worker_ctl.status()
    picks = ", ".join((scan.get("top5_tickers") or [])[:5]) or "—"
    as_of = str(scan.get("ts") or "—").replace("T", " ")[:19]
    stale = "過期" if scan.get("stale") or not scan.get("available") else "新鮮"
    skips = format_skip_lines(st.get("skip_counts") or {}, lang="zh") or "—"

    day_pnl = None
    equity = None
    try:
        from backend.trading.alpaca_broker import AlpacaBroker

        summary = AlpacaBroker().get_account_summary()
        day_pnl = summary.day_pnl
        equity = summary.equity or summary.portfolio_value
    except Exception as exc:
        logger.debug("digest equity skipped: %s", exc)

    alpha_net = None
    try:
        from backend.api import paper_performance

        paper = paper_performance.build_paper_report(persist=True)
        alpha_net = (paper.get("benchmark") or {}).get("alpha_net_of_costs_pct")
    except Exception as exc:
        logger.debug("digest paper report skipped: %s", exc)

    lines = [
        "📋 <b>收盤摘要 Post-close</b>",
        "",
        f"<b>掃描:</b> {stale} · as-of {as_of}",
        f"<b>Top5:</b> {picks}",
        f"<b>為何沒買:</b> {skips}",
        f"<b>策略:</b> {st.get('strategy') or '—'}",
    ]
    if day_pnl is not None:
        lines.append(f"<b>Day P&amp;L:</b> ${float(day_pnl):,.2f}")
    if equity is not None:
        lines.append(f"<b>Equity:</b> ${float(equity):,.2f}")
    if alpha_net is not None:
        lines.append(f"<b>vs SPY (net cost):</b> {float(alpha_net):+.2f}%")
    lines.append("")
    lines.append("紙上模擬 · 非正式投資建議")

    ok = send_plain(settings, "\n".join(lines))
    if ok:
        _set_state(last_digest_at=et.astimezone(timezone.utc))
    return ok


def trigger_scan(*, reason: str = "scheduler") -> Dict[str, Any]:
    """Start a Scan job if none is already running. Safe to call from scheduler or tests."""
    from backend.api import research_jobs

    cfg = config()
    existing = research_jobs.latest_job("scan")
    if existing and existing.get("status") in ("queued", "running"):
        _set_state(
            last_job_id=str(existing.get("id") or ""),
            last_error=None,
            next_scan_at=next_slot_after(
                _now_et(), cfg["interval_min"], outside_hours=cfg["outside_hours"]
            ),
        )
        return {"job": existing, "already_running": True}

    job = research_jobs.start_scan(
        lang=cfg["lang"],
        llm_weight=cfg["llm_weight"],
        use_llm=cfg["use_llm"],
        force_refresh=False,
    )
    job_id = str(job.get("id") or "")
    _set_state(
        last_trigger_at=datetime.now(timezone.utc),
        last_job_id=job_id,
        last_error=None,
        next_scan_at=next_slot_after(
            _now_et(), cfg["interval_min"], outside_hours=cfg["outside_hours"]
        ),
    )
    logger.info(
        "auto-scan %s job=%s status=%s llm=%s",
        reason,
        job_id[:8],
        job.get("status"),
        cfg["use_llm"],
    )
    _maybe_notify(f"自動掃描已開始（{job_id[:8]}…）· LLM={'開' if cfg['use_llm'] else '關'}")
    return {"job": job, "already_running": False}


def _loop() -> None:
    cfg0 = config()
    _set_state(
        enabled=cfg0["enabled"],
        running=True,
        interval_min=cfg0["interval_min"],
        use_llm=cfg0["use_llm"],
        next_scan_at=next_slot_after(
            _now_et(), cfg0["interval_min"], outside_hours=cfg0["outside_hours"]
        ),
    )
    logger.info(
        "scan scheduler started auto=%s interval=%sm llm=%s",
        cfg0["enabled"],
        cfg0["interval_min"],
        cfg0["use_llm"],
    )
    while not _stop.wait(20):
        cfg = config()
        _set_state(
            enabled=cfg["enabled"],
            interval_min=cfg["interval_min"],
            use_llm=cfg["use_llm"],
            next_scan_at=next_slot_after(
                _now_et(), cfg["interval_min"], outside_hours=cfg["outside_hours"]
            ),
        )
        if not cfg["enabled"]:
            continue
        with _lock:
            last = _state.get("last_trigger_at")
        try:
            slot = due_slot(
                _now_et(),
                interval_min=cfg["interval_min"],
                outside_hours=cfg["outside_hours"],
                last_trigger_at=last if isinstance(last, datetime) else None,
            )
            if slot is None:
                continue
            out = trigger_scan(reason="scheduler")
            # Post-close slot (~16:10 ET): send digest after kicking Scan
            if slot.hour == POST_CLOSE.hour and slot.minute == POST_CLOSE.minute:
                try:
                    send_post_close_digest()
                except Exception as dig_exc:
                    logger.warning("post-close digest failed: %s", dig_exc)
            _ = out
        except Exception as exc:
            logger.exception("auto-scan failed: %s", exc)
            _set_state(last_error=str(exc))
    _set_state(running=False)
    logger.info("scan scheduler stopped")


def start_background() -> Any:
    """Start daemon thread. Returns a stop callable (same pattern as telegram_inbox)."""
    global _thread
    cfg = config()
    if not cfg["enabled"]:
        _set_state(enabled=False, running=False, next_scan_at=None)
        logger.info("scan scheduler idle (SCAN_AUTO not enabled)")

        def _noop() -> None:
            return None

        return _noop

    if _thread and _thread.is_alive():
        def _already() -> None:
            _stop.set()

        return _already

    _stop.clear()
    _thread = threading.Thread(target=_loop, name="scan-scheduler", daemon=True)
    _thread.start()

    def _stop_fn() -> None:
        _stop.set()
        t = _thread
        if t and t.is_alive():
            t.join(timeout=2.0)

    return _stop_fn
