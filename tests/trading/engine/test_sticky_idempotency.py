"""502 sticky idempotency — fail-cases that double-submitted on pre-fix main.

Pre-fix: a broker/gateway 502 (or journal 502 after the broker may have
accepted) was followed by a freshly minted idempotency key, and the retry
called the broker again as a second independent order.

These tests fail on that main: the new key must not reach the broker.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from backend.trading.engine.desk_orders import place_desk_order, reset_desk_mandate_gate
from backend.trading.engine.order_manager import InMemoryOrderStore, OrderManager
from backend.trading.engine.sticky_idempotency import DO_NOT_BLIND_RETRY
from backend.trading.models import AccountSummary, Order, OrderSide, OrderStatus, OrderType
from backend.trading.safety import audit, kill_switch
from backend.utils.provenance import journal_store


TIGHT = {
    "allowed_symbols": ["AAPL", "MSFT"],
    "blocked_symbols": ["GME"],
    "max_notional_per_order": 50_000.0,
    "max_daily_orders": 50,
    "allowed_sides": ["buy", "sell"],
}


class Gateway502(Exception):
    def __init__(self, message: str = "502 Bad Gateway"):
        super().__init__(message)
        self.status_code = 502


def _order(**kwargs) -> Order:
    payload = dict(
        id="id-fresh-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1,
        limit_price=150.0,
        status=OrderStatus.RISK_APPROVED,
        idempotency_key="fresh-1",
    )
    payload.update(kwargs)
    return Order(**payload)


def _account() -> AccountSummary:
    return AccountSummary(
        cash=50_000.0,
        buying_power=50_000.0,
        portfolio_value=100_000.0,
        positions=[],
    )


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    journal = tmp_path / "trade_journal.json"
    mandate = tmp_path / "mandate.json"
    mandate.write_text(json.dumps(TIGHT), encoding="utf-8")
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH")
    reset_desk_mandate_gate()
    yield {"mandate": mandate, "journal": journal}
    reset_desk_mandate_gate()


def test_order_manager_502_then_new_key_does_not_second_submit():
    """Pre-fix main: second submit_new_order with a new key calls the broker again."""
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        raise Gateway502()

    broker = MagicMock()
    broker.submit_order.side_effect = _submit
    manager = OrderManager(broker=broker, store=InMemoryOrderStore())

    first = manager.submit_new_order(_order())
    assert first.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert first.status != OrderStatus.REJECTED
    assert first.status != OrderStatus.FILLED
    assert first.status != OrderStatus.SUBMITTED
    assert first.idempotency_sticky is True
    assert first.filled_avg_price is None
    assert "do not blind-retry" in (first.error_message or "").lower()

    second = manager.submit_new_order(
        _order(id="id-fresh-2", idempotency_key="fresh-2")
    )
    assert keys == ["fresh-1"]
    assert "fresh-2" not in keys
    assert broker.submit_order.call_count == 1
    assert second.idempotency_key == "fresh-1"
    assert second.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert second.status != OrderStatus.FILLED


def test_order_manager_same_key_safe_retry_reuses_key_not_a_new_one():
    """Safe retry resubmits the original client_order_id. It does not mint one."""
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        if len(keys) == 1:
            raise Gateway502("gateway timeout")
        order.status = OrderStatus.SUBMITTED
        return order

    broker = MagicMock()
    broker.submit_order.side_effect = _submit
    manager = OrderManager(broker=broker, store=InMemoryOrderStore())
    first = manager.submit_new_order(_order())
    assert first.status == OrderStatus.ACCEPTANCE_UNKNOWN

    second = manager.submit_new_order(_order(id="id-other", idempotency_key="fresh-1"))
    assert keys == ["fresh-1", "fresh-1"]
    assert second.status == OrderStatus.SUBMITTED
    assert second.idempotency_key == "fresh-1"
    assert second.filled_avg_price is None


def test_definite_reject_still_allows_a_new_key():
    """A known broker reject is not sticky. 502 must not swallow this path."""
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        order.status = OrderStatus.REJECTED
        order.error_message = "insufficient buying power"
        return order

    broker = MagicMock()
    broker.submit_order.side_effect = _submit
    manager = OrderManager(broker=broker, store=InMemoryOrderStore())
    first = manager.submit_new_order(_order())
    assert first.status == OrderStatus.REJECTED
    assert first.idempotency_sticky is False
    second = manager.submit_new_order(_order(id="id-fresh-2", idempotency_key="fresh-2"))
    assert second.status == OrderStatus.REJECTED
    assert keys == ["fresh-1", "fresh-2"]


def test_desk_broker_502_then_new_key_does_not_second_submit(isolated):
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        raise Gateway502()

    broker = MagicMock()
    broker.get_account_summary.return_value = _account()
    broker.submit_order.side_effect = _submit
    store = InMemoryOrderStore()
    path = isolated["mandate"]

    first = place_desk_order(
        _order(status=OrderStatus.DRAFT),
        broker=broker,
        store=store,
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert first.http_status == 502
    assert first.http_status != 200
    assert first.order.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert first.order.status != OrderStatus.FILLED
    detail = first.detail
    assert detail["retry"] == DO_NOT_BLIND_RETRY
    assert detail["acceptance"] == "unknown"
    assert detail["broker_submitted"] is None
    assert detail["idempotency_key"] == "fresh-1"
    assert "do not blind-retry" in detail["message"].lower()

    second = place_desk_order(
        _order(id="id-fresh-2", idempotency_key="fresh-2", status=OrderStatus.DRAFT),
        broker=broker,
        store=store,
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert broker.submit_order.call_count == 1
    assert keys == ["fresh-1"]
    assert second.http_status == 502
    assert second.http_status != 200
    assert second.order.idempotency_key == "fresh-1"
    assert second.order.status != OrderStatus.FILLED
    assert second.detail["retry"] == DO_NOT_BLIND_RETRY
    assert second.detail["idempotency_key"] == "fresh-1"


def test_desk_journal_502_then_new_key_does_not_second_submit(isolated, monkeypatch):
    """Journal failure after broker accept is HTTP 502. A new key must not resubmit."""
    def _boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _boom)
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        order.status = OrderStatus.SUBMITTED
        order.id = "broker-1"
        return order

    broker = MagicMock()
    broker.get_account_summary.return_value = _account()
    broker.submit_order.side_effect = _submit
    store = InMemoryOrderStore()
    path = isolated["mandate"]

    first = place_desk_order(
        _order(status=OrderStatus.DRAFT),
        broker=broker,
        store=store,
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert first.http_status == 502
    assert first.detail["broker_submitted"] is True
    assert first.detail["retry"] == DO_NOT_BLIND_RETRY
    assert first.order.idempotency_sticky is True

    second = place_desk_order(
        _order(id="id-fresh-2", idempotency_key="fresh-2", status=OrderStatus.DRAFT),
        broker=broker,
        store=store,
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert broker.submit_order.call_count == 1
    assert keys == ["fresh-1"]
    assert second.http_status == 502
    assert second.http_status != 200
    assert second.detail["retry"] == DO_NOT_BLIND_RETRY
    assert second.detail["idempotency_key"] == "fresh-1"
    assert second.order.status != OrderStatus.FILLED
    assert "not journaled" in second.detail["message"].lower()


def test_desk_same_key_after_broker_502_is_the_safe_retry(isolated):
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        if len(keys) == 1:
            raise Gateway502()
        order.status = OrderStatus.SUBMITTED
        order.id = "broker-safe"
        return order

    broker = MagicMock()
    broker.get_account_summary.return_value = _account()
    broker.submit_order.side_effect = _submit
    store = InMemoryOrderStore()
    path = isolated["mandate"]
    first = place_desk_order(
        _order(status=OrderStatus.DRAFT),
        broker=broker,
        store=store,
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert first.http_status == 502
    second = place_desk_order(
        _order(status=OrderStatus.DRAFT, idempotency_key="fresh-1"),
        broker=broker,
        store=store,
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert keys == ["fresh-1", "fresh-1"]
    assert second.http_status == 200
    assert second.order.idempotency_key == "fresh-1"
    assert second.order.idempotency_sticky is False
