"""Desk + worker shared RiskEngine extras (O5 cooldown / daily-loss).

Worker buy passes ``cooldown_tickers`` and records day-start on its
RiskEngine instance. Desk historically skipped both, so a worker refuse
could still be HTTP 200 on Desk. These files under DATA_DIR are the
same class of durable book as R4 daily orders: first write of the
calendar day wins for day-start; cooldown is 24h after a venue sell.

Correlation still needs OHLCV at evaluate time (passed by the caller).
This module does not fetch bars and does not change Kelly / M8 size.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

COOLDOWN_HOURS = 24
DAY_START_FILE = Path(DATA_DIR) / "risk_day_start.json"
COOLDOWN_FILE = Path(DATA_DIR) / "risk_cooldown.json"

_thread = threading.RLock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _exclusive(path: Path):
    lock_path = str(path) + ".lock"
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)

    class _Guard:
        def __enter__(self_inner):
            _thread.acquire()
            self_inner.fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(self_inner.fd, fcntl.LOCK_EX)
            return self_inner

        def __exit__(self_inner, exc_type, exc, tb):
            try:
                fcntl.flock(self_inner.fd, fcntl.LOCK_UN)
            finally:
                os.close(self_inner.fd)
                _thread.release()
            return False

    return _Guard()


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        logger.warning("shared risk state unreadable: %s", path, exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def record_day_start(
    portfolio_value: float,
    *,
    path: Optional[Path] = None,
    today: Optional[date] = None,
) -> Tuple[date, float]:
    """First portfolio value of ``date.today()`` wins (worker + Desk).

    A later Desk place must not reset the start to the already-dropped
    mark; that is the daily-loss dual-path hole.
    """
    target = Path(path) if path is not None else Path(DAY_START_FILE)
    day = today or date.today()
    try:
        value = float(portfolio_value)
    except (TypeError, ValueError):
        value = 0.0
    with _exclusive(target):
        existing = _read_json(target)
        stored_day = None
        stored_value = None
        if existing:
            raw_day = existing.get("date")
            try:
                stored_day = date.fromisoformat(str(raw_day))
            except (TypeError, ValueError):
                stored_day = None
            try:
                stored_value = float(existing.get("portfolio_value"))
            except (TypeError, ValueError):
                stored_value = None
        if stored_day == day and stored_value is not None and stored_value > 0:
            return stored_day, stored_value
        _write_json(
            target,
            {"date": day.isoformat(), "portfolio_value": value, "tz": "local-date.today"},
        )
        return day, value


def load_day_start(*, path: Optional[Path] = None) -> Optional[Tuple[date, float]]:
    target = Path(path) if path is not None else Path(DAY_START_FILE)
    with _exclusive(target):
        existing = _read_json(target)
    if not existing:
        return None
    try:
        stored_day = date.fromisoformat(str(existing.get("date")))
        stored_value = float(existing.get("portfolio_value"))
    except (TypeError, ValueError):
        return None
    if stored_value <= 0:
        return None
    return stored_day, stored_value


def mark_cooldown(
    symbol: str,
    *,
    when: Optional[datetime] = None,
    path: Optional[Path] = None,
) -> None:
    """Record a venue sell so worker buy and Desk buy share the 24h cool-down."""
    token = str(symbol or "").upper().strip()
    if not token:
        return
    ts = when or _utc_now()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    target = Path(path) if path is not None else Path(COOLDOWN_FILE)
    iso = ts.astimezone(timezone.utc).isoformat()
    with _exclusive(target):
        existing = _read_json(target) or {}
        symbols = existing.get("symbols")
        if not isinstance(symbols, dict):
            symbols = {}
        symbols[token] = iso
        _write_json(target, {"symbols": symbols})


def active_cooldown_tickers(
    *,
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
    hours: float = COOLDOWN_HOURS,
) -> List[str]:
    """Symbols still inside the cool-down window."""
    target = Path(path) if path is not None else Path(COOLDOWN_FILE)
    when = now or _utc_now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    window = max(0.0, float(hours)) * 3600.0
    with _exclusive(target):
        existing = _read_json(target) or {}
        symbols = existing.get("symbols")
        if not isinstance(symbols, dict):
            return []
        live: Dict[str, str] = {}
        active: List[str] = []
        for raw_sym, raw_ts in symbols.items():
            token = str(raw_sym or "").upper().strip()
            if not token:
                continue
            try:
                stamped = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if stamped.tzinfo is None:
                stamped = stamped.replace(tzinfo=timezone.utc)
            if (when - stamped).total_seconds() < window:
                live[token] = stamped.astimezone(timezone.utc).isoformat()
                active.append(token)
        if live != symbols:
            _write_json(target, {"symbols": live})
        return active
