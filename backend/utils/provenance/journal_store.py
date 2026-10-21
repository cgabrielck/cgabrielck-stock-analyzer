"""Append-only trade journal file used by GET/POST /api/journal."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.utils.constants import DATA_DIR
from backend.utils.provenance.trade_log import TRADE_LOG_SCHEMA, normalize_trade_log

JOURNAL_PATH = Path(DATA_DIR) / "trade_journal.json"
_LOCK = threading.RLock()


def journal_path() -> Path:
    return JOURNAL_PATH


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_raw_entries(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    target = path or JOURNAL_PATH
    if not target.exists():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict):
        rows = raw.get("entries") or raw.get("trades") or []
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def save_entries(entries: List[Dict[str, Any]], path: Optional[Path] = None) -> None:
    target = path or JOURNAL_PATH
    _ensure_parent(target)
    payload = {"schema_version": TRADE_LOG_SCHEMA, "entries": list(entries)}
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


def append_entry(entry: Dict[str, Any], path: Optional[Path] = None) -> Dict[str, Any]:
    """Append one TradeLogEntry-shaped row. Never rewrites history in place."""
    target = path or JOURNAL_PATH
    with _LOCK:
        rows = load_raw_entries(target)
        normalized = normalize_trade_log(entry) or entry
        rows.append(normalized)
        save_entries(rows, target)
    return normalized


def find_entry(log_id: str, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    if not log_id:
        return None
    for row in load_raw_entries(path):
        entry = normalize_trade_log(row)
        if entry and entry.get("log_id") == log_id:
            return entry
        if isinstance(row, dict) and str(row.get("id") or "") == log_id:
            return normalize_trade_log(row)
    return None
