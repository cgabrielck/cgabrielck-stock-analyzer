"""JSON IntentStore — durable L1 intent ledger.

Storage choice: JSON file with RLock + atomic replace, matching JSONOrderStore
(backend/trading/storage.py). Intents are short-lived L1 records (H3 write,
H1 consume, then expire). SQLite remains the L2 OrderStore default and is
intentionally not reused here so Order fields cannot leak into Intent v0.

Not an OrderStore. Do not persist quantity / broker ids / fills.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import List, Optional

from backend.trading.intents.models import Intent, IntentStatus, IntentValidationError, parse_intent
from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_FILENAME = "trading_intents.json"


def default_intent_path() -> Path:
    data_path = Path(DATA_DIR)
    data_path.mkdir(parents=True, exist_ok=True)
    return data_path / DEFAULT_FILENAME


class JSONIntentStore:
    """File-backed IntentStore. Thread-safe; durable across process restarts."""

    def __init__(self, filepath: Optional[str] = None):
        self.filepath = str(filepath or default_intent_path())
        Path(self.filepath).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._by_id: dict[str, Intent] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            self._by_id = {}
            if not os.path.exists(self.filepath):
                return
            try:
                with open(self.filepath, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("IntentStore load failed for %s: %s", self.filepath, exc)
                return
            rows = data
            if isinstance(data, dict):
                rows = data.get("intents") or data.get("entries") or []
            if not isinstance(rows, list):
                return
            for item in rows:
                if not isinstance(item, dict):
                    continue
                try:
                    intent = parse_intent(item)
                except IntentValidationError as exc:
                    logger.warning("Skipping invalid intent in store: %s", exc)
                    continue
                self._by_id[intent.intent_id] = intent

    def _save(self) -> None:
        with self._lock:
            payload = {
                "schema_version": "intent.v0",
                "intents": [intent.model_dump_json_dict() for intent in self._by_id.values()],
            }
            temp_file = str(self.filepath) + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, default=str)
            os.replace(temp_file, self.filepath)

    def save(self, intent: Intent) -> Intent:
        parsed = parse_intent(intent)
        with self._lock:
            self._by_id[parsed.intent_id] = parsed
            self._save()
        return parsed

    def get(self, intent_id: str) -> Optional[Intent]:
        token = str(intent_id or "").strip()
        if not token:
            return None
        with self._lock:
            return self._by_id.get(token)

    def list(
        self,
        *,
        status: Optional[str] = None,
        symbol: Optional[str] = None,
        limit: int = 100,
    ) -> List[Intent]:
        want_status = str(status or "").strip().lower() or None
        want_symbol = str(symbol or "").strip().upper() or None
        cap = max(1, int(limit or 100))
        with self._lock:
            rows = list(self._by_id.values())
        if want_status:
            rows = [row for row in rows if row.status.value == want_status]
        if want_symbol:
            rows = [row for row in rows if row.symbol == want_symbol]
        rows.sort(key=lambda row: row.as_of, reverse=True)
        return rows[:cap]

    def set_status(self, intent_id: str, status: IntentStatus) -> Optional[Intent]:
        with self._lock:
            current = self._by_id.get(str(intent_id or "").strip())
            if current is None:
                return None
            updated = current.model_copy(update={"status": status})
            self._by_id[updated.intent_id] = updated
            self._save()
            return updated

    def export_dicts(self) -> List[dict]:
        with self._lock:
            return [intent.model_dump_json_dict() for intent in self._by_id.values()]


def create_intent_store(filepath: Optional[str] = None) -> JSONIntentStore:
    """Factory — JSON file ledger (see module docstring for why not SQLite)."""
    return JSONIntentStore(filepath=filepath)
