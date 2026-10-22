"""502 sticky idempotency — fail-cases that double-submitted on pre-fix main.

Pre-fix: a broker/gateway 502 (or journal 502 after the broker may have
accepted) was followed by a freshly minted idempotency key, and the retry
called the broker again as a second independent order.

These tests fail on that main: the new key must not reach the broker.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from backend.trading.alpaca_broker import disable_unsafe_place_retries
from backend.trading.engine.desk_orders import (
    DESK_ACTOR,
    DESK_STRATEGY_ID,
    DESK_WRITE_POINT,
    place_desk_order,
    reset_desk_mandate_gate,
)
from backend.trading.engine.order_manager import InMemoryOrderStore, OrderManager
from backend.trading.engine.sticky_idempotency import (
    DO_NOT_BLIND_RETRY,
    StickyLookupUnproven,
    find_sticky_order,
)
from backend.trading.models import AccountSummary, Order, OrderSide, OrderStatus, OrderType
from backend.trading.safety import audit, kill_switch
from backend.trading.storage import SQLiteOrderStore
from backend.utils.provenance import journal_store


TIGHT = {
    "allowed_symbols": ["AAPL", "MSFT"],
    "blocked_symbols": ["GME"],
    "max_notional_per_order": 50_000.0,
    "max_daily_orders": 50,
    "allowed_sides": ["buy", "sell"],
}


class Gateway502(Exception):
    def __init__(self, message: str = "502 Bad Gateway", status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class Unique422(Exception):
    """Alpaca does not hand back the original order on this body."""

    def __init__(self):
        super().__init__('{"code":42210000,"message":"client_order_id must be unique"}')
        self.status_code = 422


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


def _place(store, broker, mandate, **order_kwargs):
    return place_desk_order(
        _order(status=OrderStatus.DRAFT, **order_kwargs),
        broker=broker,
        store=store,
        mandate_path=mandate,
        quote_fn=lambda _s: 150.0,
    )


@pytest.mark.parametrize("status_code,message", [(502, "502 Bad Gateway"), (504, "504 Gateway Timeout")])
def test_desk_unknown_then_422_unique_keeps_sticky_and_blocks_fresh_key(isolated, status_code, message):
    """5f21c71 treated same-key 422 unique as rejected and cleared sticky.

    Desk repro (max_daily_orders=50): 502 → same key → HTTP 400 → fresh-2 HTTP 200,
    and the broker saw three client_order_ids. The 422 must stay unknown.
    """
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        if keys.count(order.idempotency_key) == 1:
            raise Gateway502(message, status_code=status_code)
        raise Unique422()

    broker = MagicMock()
    broker.get_account_summary.return_value = _account()
    broker.submit_order.side_effect = _submit
    store = InMemoryOrderStore()
    path = isolated["mandate"]

    first = _place(store, broker, path)
    assert first.http_status == 502
    assert first.order.idempotency_sticky is True

    second = _place(store, broker, path, idempotency_key="fresh-1")
    assert second.http_status == 502
    assert second.http_status != 400
    assert second.order.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert second.order.status != OrderStatus.REJECTED
    assert second.order.idempotency_sticky is True
    assert second.order.filled_avg_price is None
    assert second.detail["retry"] == DO_NOT_BLIND_RETRY
    assert second.detail["acceptance"] == "unknown"
    assert second.detail["idempotency_key"] == "fresh-1"

    third = _place(store, broker, path, id="id-fresh-2", idempotency_key="fresh-2")
    assert third.http_status == 502
    assert third.http_status != 200
    assert "fresh-2" not in keys
    assert set(keys) == {"fresh-1"}
    assert third.order.idempotency_key == "fresh-1"
    assert third.detail["retry"] == DO_NOT_BLIND_RETRY
    broker.submit_order.assert_called()
    assert broker.submit_order.call_count == 2


def test_first_seen_422_unique_pins_sticky_without_a_prior_502():
    """alpaca-py can retry a 504 into 422 before we observe the timeout."""
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        if order.idempotency_key == "fresh-1":
            raise Unique422()
        order.status = OrderStatus.SUBMITTED
        return order

    broker = MagicMock()
    broker.submit_order.side_effect = _submit
    manager = OrderManager(broker=broker, store=InMemoryOrderStore())
    first = manager.submit_new_order(_order())
    assert first.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert first.idempotency_sticky is True
    assert first.filled_avg_price is None
    second = manager.submit_new_order(_order(id="id-fresh-2", idempotency_key="fresh-2"))
    assert "fresh-2" not in keys
    assert second.idempotency_key == "fresh-1"
    assert broker.submit_order.call_count == 1


def test_other_422_is_still_a_definite_reject():
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        err = RuntimeError('{"message": "insufficient qty available"}')
        err.status_code = 422
        raise err

    broker = MagicMock()
    broker.submit_order.side_effect = _submit
    manager = OrderManager(broker=broker, store=InMemoryOrderStore())
    first = manager.submit_new_order(_order())
    assert first.status == OrderStatus.REJECTED
    assert first.idempotency_sticky is False
    second = manager.submit_new_order(_order(id="id-fresh-2", idempotency_key="fresh-2"))
    assert keys == ["fresh-1", "fresh-2"]
    assert second.status == OrderStatus.REJECTED


def test_returned_reject_body_for_unique_client_order_id_stays_sticky():
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        order.status = OrderStatus.REJECTED
        order.error_message = '{"code":42210000,"message":"client_order_id must be unique"}'
        return order

    broker = MagicMock()
    broker.submit_order.side_effect = _submit
    manager = OrderManager(broker=broker, store=InMemoryOrderStore())
    first = manager.submit_new_order(_order())
    assert first.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert first.idempotency_sticky is True
    second = manager.submit_new_order(_order(id="id-fresh-2", idempotency_key="fresh-2"))
    assert "fresh-2" not in keys
    assert second.idempotency_key == "fresh-1"


def test_desk_journal_late_success_keeps_sticky_so_new_key_cannot_second_submit(isolated, monkeypatch):
    """Journal catch-up may be HTTP 200 with no broker call. Sticky must stay."""
    calls = {"n": 0}

    def _journal(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk full")
        # Catch-up 200 still requires the O5/PATH stamp. A bare {"id": ...}
        # is not an honest journal success.
        return {
            "intent_id": "desk-corr-late",
            "strategy_id": DESK_STRATEGY_ID,
            "links": {"intent_ref": ""},
            "provenance": {
                "write_point": DESK_WRITE_POINT,
                "actor": DESK_ACTOR,
                "verified": False,
                "live": False,
            },
        }

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _journal)
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

    first = _place(store, broker, path)
    assert first.http_status == 502
    assert first.order.idempotency_sticky is True
    assert broker.submit_order.call_count == 1

    caught_up = _place(store, broker, path, id="id-fresh-2", idempotency_key="fresh-2")
    assert caught_up.http_status == 200
    assert caught_up.order.idempotency_sticky is True
    assert caught_up.order.idempotency_key == "fresh-1"
    assert broker.submit_order.call_count == 1

    later = _place(store, broker, path, id="id-fresh-3", idempotency_key="fresh-3")
    assert broker.submit_order.call_count == 1
    assert keys == ["fresh-1"]
    assert later.order.idempotency_key == "fresh-1"
    assert later.order.idempotency_sticky is True
    assert "fresh-2" not in keys and "fresh-3" not in keys


def test_sqlite_sticky_survives_more_than_1000_newer_orders(tmp_path):
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    store = SQLiteOrderStore(filepath=str(tmp_path / "orders.sqlite3"))
    sticky = _order(
        id="sticky-1",
        idempotency_key="sticky-key",
        status=OrderStatus.ACCEPTANCE_UNKNOWN,
        idempotency_sticky=True,
    )
    sticky.updated_at = base
    sticky.created_at = base
    store.save_order(sticky)
    for i in range(1001):
        other = _order(
            id=f"other-{i}",
            symbol="MSFT",
            idempotency_key=f"other-{i}",
            status=OrderStatus.SUBMITTED,
            idempotency_sticky=False,
        )
        stamp = base + timedelta(seconds=i + 1)
        other.updated_at = stamp
        other.created_at = stamp
        store.save_order(other)

    recent = store.get_recent_orders(1000)
    assert all(order.idempotency_key != "sticky-key" for order in recent)
    found = find_sticky_order(store, symbol="AAPL", side="buy")
    assert found is not None
    assert found.idempotency_key == "sticky-key"

    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        order.status = OrderStatus.SUBMITTED
        return order

    broker = MagicMock()
    broker.submit_order.side_effect = _submit
    manager = OrderManager(broker=broker, store=store)
    blocked = manager.submit_new_order(
        _order(id="id-fresh-2", idempotency_key="fresh-2", status=OrderStatus.RISK_APPROVED)
    )
    assert keys == []
    broker.submit_order.assert_not_called()
    assert blocked.idempotency_key == "sticky-key"
    store.close()


def test_recent_window_store_fails_closed_instead_of_submitting():
    class RecentOnly:
        def __init__(self):
            self.saved = []

        def get_recent_orders(self, limit=50):
            return []

        def get_order_by_idempotency_key(self, key):
            return None

        def save_order(self, order):
            self.saved.append(order)

    broker = MagicMock()
    manager = OrderManager(broker=broker, store=RecentOnly())
    result = manager.submit_new_order(_order())
    broker.submit_order.assert_not_called()
    assert result.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert result.idempotency_sticky is True
    assert isinstance(result.error_message, str)
    with pytest.raises(StickyLookupUnproven):
        find_sticky_order(RecentOnly(), symbol="AAPL", side="buy")


def test_desk_unproven_lookup_does_not_broker_submit(isolated):
    class RecentOnly:
        def __init__(self):
            self.saved = []

        def get_recent_orders(self, limit=50):
            return []

        def get_order_by_idempotency_key(self, key):
            return None

        def save_order(self, order):
            self.saved.append(order)

    broker = MagicMock()
    broker.get_account_summary.return_value = _account()
    result = _place(RecentOnly(), broker, isolated["mandate"])
    assert result.http_status == 502
    assert result.order.idempotency_sticky is True
    assert result.detail["retry"] == DO_NOT_BLIND_RETRY
    broker.submit_order.assert_not_called()


def _http_response(status_code: int, text: str):
    class _Resp:
        def __init__(self):
            self.status_code = status_code
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        def json(self):
            return {}

    return _Resp()


def test_alpaca_py_retries_504_into_422_until_place_retry_is_disabled(monkeypatch):
    """Installed client auto-POSTs 504. The follow-up can be 422 unique."""
    monkeypatch.setattr("alpaca.common.rest.time.sleep", lambda *_a, **_k: None)
    client = TradingClient("k", "s", paper=True)
    assert 504 in client._retry_codes
    assert 429 in client._retry_codes
    posts = {"n": 0}

    def _request(method, url, **_opts):
        posts["n"] += 1
        if posts["n"] == 1:
            return _http_response(504, "gateway timeout")
        return _http_response(422, '{"code":42210000,"message":"client_order_id must be unique"}')

    monkeypatch.setattr(client._session, "request", _request)
    req = MarketOrderRequest(
        symbol="AAPL",
        qty=1,
        side=AlpacaOrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        client_order_id="landed-1",
    )
    with pytest.raises(APIError) as caught:
        client.submit_order(order_data=req)
    assert posts["n"] == 2
    assert caught.value.status_code == 422
    assert "client_order_id" in str(caught.value)

    posts["n"] = 0
    disable_unsafe_place_retries(client)
    assert 504 not in client._retry_codes
    assert 429 in client._retry_codes
    with pytest.raises(APIError) as once:
        client.submit_order(order_data=req)
    assert posts["n"] == 1
    assert once.value.status_code == 504


def _pin_with_broker_id(order):
    """What Alpaca's 422 lookup does: keep the key, replace the local id."""
    order.id = "broker-desk-landed"
    order.status = OrderStatus.ACCEPTANCE_UNKNOWN
    order.idempotency_sticky = True
    order.error_message = '{"code":42210000,"message":"client_order_id must be unique"}'
    order.filled_avg_price = None
    order.filled_at = None
    return order


def test_postgres_save_order_keeps_sticky_when_id_is_remapped(postgres_store):
    """ON CONFLICT (id) misses the row after the local id becomes the broker id."""
    local_id = "local-desk-1"
    draft = _order(id=local_id, idempotency_key="fresh-1", status=OrderStatus.RISK_APPROVED)
    postgres_store.save_order(draft)
    pinned = draft.model_copy(
        update={
            "id": "broker-desk-landed",
            "status": OrderStatus.ACCEPTANCE_UNKNOWN,
            "idempotency_sticky": True,
            "error_message": "client_order_id must be unique",
            "filled_avg_price": None,
        }
    )
    postgres_store.save_order(pinned)
    saved = postgres_store.get_order_by_idempotency_key("fresh-1")
    assert saved is not None
    assert saved.id == "broker-desk-landed"
    assert saved.idempotency_sticky is True
    assert saved.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert saved.filled_avg_price is None
    assert postgres_store.get_order(local_id) is None
    assert postgres_store.get_order("broker-desk-landed").idempotency_key == "fresh-1"
    assert len(postgres_store.iter_all_orders()) == 1


def test_postgres_save_order_still_updates_the_same_id(postgres_store):
    original = _order(id="same-id", idempotency_key="key-a", status=OrderStatus.DRAFT)
    postgres_store.save_order(original)
    rewritten = original.model_copy(
        update={"idempotency_key": "key-b", "status": OrderStatus.SUBMITTED}
    )
    postgres_store.save_order(rewritten)
    by_id = postgres_store.get_order("same-id")
    assert by_id is not None
    assert by_id.idempotency_key == "key-b"
    assert by_id.status == OrderStatus.SUBMITTED
    assert postgres_store.get_order_by_idempotency_key("key-a") is None
    assert postgres_store.get_order_by_idempotency_key("key-b").id == "same-id"
    assert len(postgres_store.iter_all_orders()) == 1


def test_desk_postgres_422_id_remap_blocks_the_next_new_key(isolated, postgres_store):
    """First-sight 422 on Postgres: the pin must survive the id rewrite."""
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        return _pin_with_broker_id(order)

    broker = MagicMock()
    broker.get_account_summary.return_value = _account()
    broker.submit_order.side_effect = _submit
    path = isolated["mandate"]

    first = _place(postgres_store, broker, path)
    assert first.http_status == 502
    assert first.order.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert first.order.status != OrderStatus.REJECTED
    assert first.order.status != OrderStatus.FILLED
    assert first.order.idempotency_sticky is True
    assert first.order.id == "broker-desk-landed"
    assert first.order.filled_avg_price is None
    assert first.detail["retry"] == DO_NOT_BLIND_RETRY
    saved = postgres_store.get_order_by_idempotency_key("fresh-1")
    assert saved is not None
    assert saved.id == "broker-desk-landed"
    assert saved.idempotency_sticky is True
    assert postgres_store.get_order("id-fresh-1") is None

    second = _place(postgres_store, broker, path, id="id-fresh-2", idempotency_key="fresh-2")
    assert broker.submit_order.call_count == 1
    assert keys == ["fresh-1"]
    assert second.http_status == 502
    assert second.http_status != 200
    assert second.order.idempotency_key == "fresh-1"
    assert second.order.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert second.order.status != OrderStatus.REJECTED
    assert second.order.idempotency_sticky is True
    assert second.detail["retry"] == DO_NOT_BLIND_RETRY
