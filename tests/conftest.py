"""Shared pytest isolation — durable R4 daily counter must not leak across tests."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_daily_order_counter(tmp_path, monkeypatch):
    from backend.trading.safety import mandate

    monkeypatch.setattr(mandate, "DAILY_ORDERS_FILE", tmp_path / "mandate_daily_orders.json")
    yield


def _postgres_sticky_url(psycopg):
    """Dedicated test database. Never truncates an application DATABASE_URL."""
    explicit = (os.environ.get("CGAB_TEST_DATABASE_URL") or "").strip()
    if explicit:
        return explicit
    admin = psycopg.connect("dbname=postgres", connect_timeout=3)
    try:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                ("cgab_sticky_test",),
            )
            if cur.fetchone() is None:
                cur.execute("CREATE DATABASE cgab_sticky_test")
    finally:
        admin.close()
    return "dbname=cgab_sticky_test"


@pytest.fixture
def postgres_store():
    """Real PostgresOrderStore. Skips only when this machine has no server.

    The 422 id-remap pin is a Postgres ``UNIQUE(idempotency_key)`` failure.
    SQLite already upserts on that key, so these tests must use Postgres.
    """
    psycopg = pytest.importorskip("psycopg")
    from backend.trading.storage import PostgresOrderStore

    try:
        url = _postgres_sticky_url(psycopg)
        probe = psycopg.connect(url, connect_timeout=3)
    except Exception as exc:
        pytest.skip(f"Postgres not reachable for sticky pin regression: {exc}")
    probe.close()
    store = PostgresOrderStore(database_url=url)
    with store._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE trading_orders")
        conn.commit()
    yield store
