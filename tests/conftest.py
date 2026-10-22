"""Shared pytest isolation — durable R4 daily counter must not leak across tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_daily_order_counter(tmp_path, monkeypatch):
    from backend.trading.safety import mandate

    monkeypatch.setattr(mandate, "DAILY_ORDERS_FILE", tmp_path / "mandate_daily_orders.json")
    yield
