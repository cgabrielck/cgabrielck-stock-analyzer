"""
Order storage backends.

JSONOrderStore — simple file ledger (tests / legacy).
SQLiteOrderStore — durable local ledger (default for worker).
PostgresOrderStore — optional when DATABASE_URL is set.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from backend.trading.models import Order, OrderStatus
from backend.trading.engine.order_manager import OrderStore
from backend.utils.constants import DATA_DIR


class JSONOrderStore:
    """
    Durable JSON-file backed store for trading orders.
    Matches the simplicity of other local storage in the app (like portfolio.json).
    """
    def __init__(self, filepath=None):
        if filepath is None:
            data_path = Path(DATA_DIR)
            data_path.mkdir(parents=True, exist_ok=True)
            self.filepath = str(data_path / "trading_orders.json")
        else:
            self.filepath = str(filepath)
            
        self._lock = threading.RLock()
        self._orders_by_id: Dict[str, Order] = {}
        self._orders_by_idem_key: Dict[str, Order] = {}
        self._load()

    def _load(self):
        with self._lock:
            if not os.path.exists(self.filepath):
                return
            try:
                with open(self.filepath, 'r') as f:
                    data = json.load(f)
                    # Accept either a bare list or {"orders": [...]}
                    if isinstance(data, dict):
                        data = data.get("orders", [])
                    for item in data:
                        order = Order.model_validate(item)
                        self._orders_by_id[order.id] = order
                        self._orders_by_idem_key[order.idempotency_key] = order
            except json.JSONDecodeError:
                pass

    def _save(self):
        with self._lock:
            data = [order.model_dump(mode='json') for order in self._orders_by_id.values()]
            temp_file = str(self.filepath) + ".tmp"
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(temp_file, self.filepath)

    def save_order(self, order: Order) -> None:
        with self._lock:
            self._orders_by_id[order.id] = order
            self._orders_by_idem_key[order.idempotency_key] = order
            self._save()

    def get_order_by_idempotency_key(self, key: str) -> Optional[Order]:
        with self._lock:
            return self._orders_by_idem_key.get(key)

    def get_order(self, order_id: str) -> Optional[Order]:
        with self._lock:
            return self._orders_by_id.get(order_id)
            
    def get_recent_orders(self, limit: int = 50) -> List[Order]:
        with self._lock:
            sorted_orders = sorted(self._orders_by_id.values(), key=lambda o: o.created_at, reverse=True)
            return sorted_orders[:limit]

    def export_dicts(self) -> List[dict]:
        with self._lock:
            return [o.model_dump(mode="json") for o in self._orders_by_id.values()]


class SQLiteOrderStore:
    """SQLite-backed durable order ledger suitable for VPS worker restarts."""

    def __init__(self, filepath: Optional[str] = None):
        if filepath is None:
            data_path = Path(DATA_DIR)
            data_path.mkdir(parents=True, exist_ok=True)
            filepath = str(data_path / "trading_orders.sqlite3")
        self.filepath = str(filepath)
        Path(self.filepath).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.filepath, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_orders_updated ON orders(updated_at DESC)"
            )
            self._conn.commit()

    def save_order(self, order: Order) -> None:
        payload = json.dumps(order.model_dump(mode="json"))
        updated = (order.updated_at or datetime.now()).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO orders (id, idempotency_key, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    idempotency_key=excluded.idempotency_key,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (order.id, order.idempotency_key, payload, updated),
            )
            self._conn.commit()

    def get_order_by_idempotency_key(self, key: str) -> Optional[Order]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM orders WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return Order.model_validate(json.loads(row["payload"])) if row else None

    def get_order(self, order_id: str) -> Optional[Order]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
        return Order.model_validate(json.loads(row["payload"])) if row else None

    def get_recent_orders(self, limit: int = 50) -> List[Order]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM orders ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [Order.model_validate(json.loads(r["payload"])) for r in rows]

    def export_dicts(self) -> List[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT payload FROM orders").fetchall()
        return [json.loads(r["payload"]) for r in rows]


class PostgresOrderStore:
    """
    Optional Postgres ledger when DATABASE_URL is configured.

    Uses psycopg (v3) if available; raises ImportError otherwise so callers
    can fall back to SQLite.
    """

    def __init__(self, database_url: Optional[str] = None):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "psycopg is required for PostgresOrderStore. "
                "Install psycopg[binary] or use ORDER_STORE_BACKEND=sqlite."
            ) from exc

        self.database_url = database_url or os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError("DATABASE_URL is required for PostgresOrderStore")
        self._psycopg = psycopg
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self):
        return self._psycopg.connect(self.database_url)

    def _init_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS trading_orders (
                            id TEXT PRIMARY KEY,
                            idempotency_key TEXT UNIQUE NOT NULL,
                            payload JSONB NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                conn.commit()

    def save_order(self, order: Order) -> None:
        payload = json.dumps(order.model_dump(mode="json"))
        with self._lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO trading_orders (id, idempotency_key, payload, updated_at)
                        VALUES (%s, %s, %s::jsonb, NOW())
                        ON CONFLICT (id) DO UPDATE SET
                            idempotency_key = EXCLUDED.idempotency_key,
                            payload = EXCLUDED.payload,
                            updated_at = NOW()
                        """,
                        (order.id, order.idempotency_key, payload),
                    )
                conn.commit()

    def get_order_by_idempotency_key(self, key: str) -> Optional[Order]:
        with self._lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT payload FROM trading_orders WHERE idempotency_key = %s",
                        (key,),
                    )
                    row = cur.fetchone()
        return Order.model_validate(row[0]) if row else None

    def get_order(self, order_id: str) -> Optional[Order]:
        with self._lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT payload FROM trading_orders WHERE id = %s",
                        (order_id,),
                    )
                    row = cur.fetchone()
        return Order.model_validate(row[0]) if row else None

    def get_recent_orders(self, limit: int = 50) -> List[Order]:
        with self._lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT payload FROM trading_orders
                        ORDER BY updated_at DESC LIMIT %s
                        """,
                        (int(limit),),
                    )
                    rows = cur.fetchall()
        return [Order.model_validate(r[0]) for r in rows]

    def export_dicts(self) -> List[dict]:
        with self._lock:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT payload FROM trading_orders")
                    rows = cur.fetchall()
        return [dict(r[0]) if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


def create_order_store(
    backend: Optional[str] = None,
    filepath: Optional[str] = None,
) -> OrderStore:
    """
    Factory for worker order ledgers.

    backend: sqlite | json | postgres (default from ORDER_STORE_BACKEND or sqlite)
    """
    choice = (backend or os.getenv("ORDER_STORE_BACKEND") or "sqlite").strip().lower()
    if choice == "json":
        return JSONOrderStore(filepath=filepath)
    if choice == "postgres":
        try:
            return PostgresOrderStore()
        except Exception:
            # Fall back to sqlite rather than crashing the worker on boot.
            path = filepath or str(Path(DATA_DIR) / "trading_orders.sqlite3")
            return SQLiteOrderStore(filepath=path)
    path = filepath or os.getenv("ORDER_STORE_PATH")
    return SQLiteOrderStore(filepath=path)
