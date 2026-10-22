"""JSON IntentStore — durable L1 intent ledger.

Storage choice: JSON file with process-shared fcntl lock, reload-before-write,
and atomic replace. Desk API and TradingWorker are separate processes; an
in-memory snapshot plus whole-file rewrite would drop concurrent updates.

SQLite remains the L2 OrderStore default and is not reused here so Order
fields cannot leak into Intent v0.

Not an OrderStore. Do not persist quantity / broker ids / fills.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

from backend.trading.intents.models import Intent, IntentStatus, IntentValidationError, parse_intent
from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_FILENAME = "trading_intents.json"
_TERMINAL = frozenset(
    {IntentStatus.CONSUMED, IntentStatus.CANCELLED, IntentStatus.EXPIRED}
)


class IntentConflictError(Exception):
    """Observable fail-closed write: revive, cancel-after-consume, or CAS miss."""

    def __init__(
        self,
        message: str,
        *,
        intent: Optional[Intent] = None,
        current_status: Optional[IntentStatus] = None,
    ):
        super().__init__(message)
        self.intent = intent
        self.current_status = current_status


def default_intent_path() -> Path:
    data_path = Path(DATA_DIR)
    data_path.mkdir(parents=True, exist_ok=True)
    return data_path / DEFAULT_FILENAME


class JSONIntentStore:
    """File-backed IntentStore. Cross-process safe via flock + merge-by-id."""

    def __init__(self, filepath: Optional[str] = None):
        self.filepath = str(filepath or default_intent_path())
        Path(self.filepath).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._by_id: dict[str, Intent] = {}
        self._load()

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Thread lock then process flock. Reload is the caller's job."""
        lock_path = str(self.filepath) + ".lock"
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _load(self) -> None:
        with self._exclusive():
            self._reload_unlocked()

    def _reload_unlocked(self) -> None:
        self._by_id = {}
        if not os.path.exists(self.filepath):
            return
        try:
            with open(self.filepath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("IntentStore load failed for %s: %s", self.filepath, exc)
            raise
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

    def _save_unlocked(self) -> None:
        payload = {
            "schema_version": "intent.v0",
            "intents": [intent.model_dump_json_dict() for intent in self._by_id.values()],
        }
        temp_file = str(self.filepath) + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_file, self.filepath)

    def _cancel_other_active_unlocked(self, intent: Intent) -> int:
        cancelled = 0
        for other in list(self._by_id.values()):
            if other.intent_id == intent.intent_id:
                continue
            if other.status is not IntentStatus.ACTIVE:
                continue
            if other.symbol != intent.symbol or other.side != intent.side:
                continue
            self._by_id[other.intent_id] = other.model_copy(
                update={"status": IntentStatus.CANCELLED}
            )
            cancelled += 1
        return cancelled

    def save(self, intent: Intent) -> Intent:
        """Merge this intent into the on-disk ledger. Refuses to revive terminals."""
        parsed = parse_intent(intent)
        with self._exclusive():
            self._reload_unlocked()
            existing = self._by_id.get(parsed.intent_id)
            if (
                existing is not None
                and existing.status in _TERMINAL
                and parsed.status is IntentStatus.ACTIVE
            ):
                raise IntentConflictError(
                    f"cannot revive {existing.status.value} intent {parsed.intent_id}; "
                    "use a new intent_id",
                    intent=existing,
                    current_status=existing.status,
                )
            self._by_id[parsed.intent_id] = parsed
            if parsed.status is IntentStatus.ACTIVE:
                self._cancel_other_active_unlocked(parsed)
            self._save_unlocked()
            return parsed

    def get(self, intent_id: str) -> Optional[Intent]:
        token = str(intent_id or "").strip()
        if not token:
            return None
        with self._exclusive():
            self._reload_unlocked()
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
        with self._exclusive():
            self._reload_unlocked()
            rows = list(self._by_id.values())
        if want_status:
            rows = [row for row in rows if row.status.value == want_status]
        if want_symbol:
            rows = [row for row in rows if row.symbol == want_symbol]
        rows.sort(key=lambda row: row.as_of, reverse=True)
        return rows[:cap]

    def set_status(self, intent_id: str, status: IntentStatus) -> Optional[Intent]:
        token = str(intent_id or "").strip()
        with self._exclusive():
            self._reload_unlocked()
            current = self._by_id.get(token)
            if current is None:
                return None
            if status is IntentStatus.ACTIVE and current.status is not IntentStatus.ACTIVE:
                raise IntentConflictError(
                    f"cannot revive {current.status.value} intent {token}; use a new intent_id",
                    intent=current,
                    current_status=current.status,
                )
            if status is IntentStatus.CANCELLED and current.status is not IntentStatus.ACTIVE:
                raise IntentConflictError(
                    f"cannot cancel intent {token} in status {current.status.value}",
                    intent=current,
                    current_status=current.status,
                )
            if status is IntentStatus.CONSUMED and current.status is not IntentStatus.ACTIVE:
                raise IntentConflictError(
                    f"cannot consume intent {token} in status {current.status.value}",
                    intent=current,
                    current_status=current.status,
                )
            if status is IntentStatus.EXPIRED and current.status is not IntentStatus.ACTIVE:
                return current
            if current.status is status:
                return current
            updated = current.model_copy(update={"status": status})
            self._by_id[updated.intent_id] = updated
            self._save_unlocked()
            return updated

    def claim_for_consume(self, intent_id: str) -> Optional[Intent]:
        """CAS active → consumed before process_signal. None if cancel/expiry won."""
        token = str(intent_id or "").strip()
        with self._exclusive():
            self._reload_unlocked()
            current = self._by_id.get(token)
            if current is None or current.status is not IntentStatus.ACTIVE:
                return None
            updated = current.model_copy(update={"status": IntentStatus.CONSUMED})
            self._by_id[updated.intent_id] = updated
            self._save_unlocked()
            return updated

    def export_dicts(self) -> List[dict]:
        with self._exclusive():
            self._reload_unlocked()
            return [intent.model_dump_json_dict() for intent in self._by_id.values()]


def create_intent_store(filepath: Optional[str] = None) -> JSONIntentStore:
    """Factory — JSON file ledger with flock merge (see module docstring)."""
    return JSONIntentStore(filepath=filepath)
