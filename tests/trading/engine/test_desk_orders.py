"""R1-SAFE Desk place-order gates (not IntentStore / SignalProcessor)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from backend.trading.engine.desk_orders import (
    DESK_STRATEGY_ID,
    KILL_BUY_DETAIL,
    NO_CONSTRAINING_PRICE_DETAIL,
    constraining_order_price,
    ops_mandate_payload,
    place_desk_order,
    reset_desk_mandate_gate,
    resolve_reference_price,
)
from backend.trading.engine.order_manager import InMemoryOrderStore
from backend.trading.models import AccountSummary, Order, OrderSide, OrderStatus, OrderType, Position
from backend.trading.risk.gates import RiskEngine, RiskLimits
from backend.trading.safety import audit, kill_switch
from backend.utils.provenance import journal_store


def _order(**kwargs):
    payload = dict(
        id="desk-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1,
        limit_price=150.0,
        idempotency_key="desk-1",
    )
    payload.update(kwargs)
    return Order(**payload)


def _account(**kwargs):
    payload = dict(
        cash=50_000.0,
        buying_power=50_000.0,
        portfolio_value=100_000.0,
        positions=[],
    )
    payload.update(kwargs)
    return AccountSummary(**payload)


def _broker(account=None):
    broker = MagicMock()
    broker.get_account_summary.return_value = account or _account()

    def _submit(order):
        order.status = OrderStatus.SUBMITTED
        order.id = "broker-1"
        return order

    broker.submit_order.side_effect = _submit
    return broker


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    journal = tmp_path / "trade_journal.json"
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH")
    reset_desk_mandate_gate()
    yield {"audit_dir": audit_dir, "journal": journal}
    reset_desk_mandate_gate()


def _mandate_file(tmp_path, payload):
    path = tmp_path / "mandate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


TIGHT = {
    "allowed_symbols": ["AAPL", "MSFT"],
    "blocked_symbols": ["GME"],
    "max_notional_per_order": 10_000.0,
    "max_daily_orders": 50,
    "allowed_sides": ["buy", "sell"],
}


def test_mandate_blocked_symbol_does_not_call_broker(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    store = InMemoryOrderStore()
    result = place_desk_order(
        _order(symbol="GME", id="gme-1", idempotency_key="gme-1"),
        broker=broker,
        store=store,
        mandate_path=path,
    )
    assert result.blocked is True
    assert result.http_status == 409
    assert result.gate == "mandate"
    assert "block" in (result.detail or "").lower()
    broker.submit_order.assert_not_called()
    saved = store.get_order("gme-1")
    assert saved is not None
    assert saved.status == OrderStatus.REJECTED
    rows = journal_store.load_raw_entries()
    assert rows
    assert any("GME" in str(row.get("symbol")) for row in rows)


def test_resolve_reference_price_ignores_unconstrained_market_limit():
    """Market limit_price is not on MarketOrderRequest — must not be the ref."""
    order = _order(order_type=OrderType.MARKET, quantity=10000, limit_price=0.01)
    assert constraining_order_price(order) is None
    px = resolve_reference_price(order, _account(), quote_fn=lambda _s: 150.0)
    assert px == 150.0


def test_resolve_reference_price_market_tiny_limit_without_quote_is_none():
    order = _order(order_type=OrderType.MARKET, limit_price=0.01)
    assert resolve_reference_price(order, _account(), quote_fn=lambda _s: None) is None


def test_resolve_reference_price_uses_position_mark_for_market():
    account = _account(
        positions=[
            Position(
                id="p1",
                symbol="AAPL",
                quantity=10,
                average_entry_price=140.0,
                current_price=148.5,
            )
        ]
    )
    order = _order(order_type=OrderType.MARKET, limit_price=0.01)
    assert resolve_reference_price(account=account, order=order, quote_fn=lambda _s: None) == 148.5


def test_market_buy_tiny_limit_price_409_does_not_hit_broker(tmp_path):
    """qty=10000 × dummy $0.01 would look like $100; real quote $150 is $1.5M."""
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            order_type=OrderType.MARKET,
            quantity=10000,
            limit_price=0.01,
            id="mkt-tiny-buy",
            idempotency_key="mkt-tiny-buy",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 409
    assert result.blocked is True
    assert result.gate in ("mandate", "risk")
    broker.submit_order.assert_not_called()
    detail = (result.detail or "").lower()
    assert "notional" in detail or "exceed" in detail


def test_market_sell_tiny_limit_price_409_does_not_hit_broker(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=10000,
            limit_price=0.01,
            id="mkt-tiny-sell",
            idempotency_key="mkt-tiny-sell",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 409
    assert result.blocked is True
    broker.submit_order.assert_not_called()
    detail = (result.detail or "").lower()
    assert "notional" in detail or "exceed" in detail or "risk" in detail


def test_resolve_reference_price_ignores_unconstrained_stop_limit():
    """Stop limit_price is not on StopOrderRequest — same ignore path as market."""
    order = _order(order_type=OrderType.STOP, quantity=10000, limit_price=0.01)
    assert constraining_order_price(order) is None
    px = resolve_reference_price(order, _account(), quote_fn=lambda _s: 150.0)
    assert px == 150.0


def test_resolve_reference_price_stop_tiny_limit_without_quote_is_none():
    order = _order(order_type=OrderType.STOP, limit_price=0.01)
    assert resolve_reference_price(order, _account(), quote_fn=lambda _s: None) is None


def test_stop_buy_tiny_limit_price_409_does_not_hit_broker(tmp_path):
    """Contract symmetry with market: stop + dummy $0.01 must use quote/mark."""
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            order_type=OrderType.STOP,
            quantity=10000,
            limit_price=0.01,
            id="stp-tiny-buy",
            idempotency_key="stp-tiny-buy",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 409
    assert result.blocked is True
    assert result.gate in ("mandate", "risk")
    broker.submit_order.assert_not_called()
    detail = (result.detail or "").lower()
    assert "notional" in detail or "exceed" in detail


def test_stop_sell_tiny_limit_price_409_does_not_hit_broker(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            side=OrderSide.SELL,
            order_type=OrderType.STOP,
            quantity=10000,
            limit_price=0.01,
            id="stp-tiny-sell",
            idempotency_key="stp-tiny-sell",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 409
    assert result.blocked is True
    broker.submit_order.assert_not_called()
    detail = (result.detail or "").lower()
    assert "notional" in detail or "exceed" in detail or "risk" in detail


def test_resolve_reference_price_sell_limit_uses_max_of_limit_and_quote():
    """Sell limit is a floor — tiny limit must not understate vs mark/quote."""
    tiny = _order(side=OrderSide.SELL, order_type=OrderType.LIMIT, limit_price=0.01)
    assert constraining_order_price(tiny) == 0.01
    assert resolve_reference_price(tiny, _account(), quote_fn=lambda _s: 150.0) == 150.0
    above = _order(side=OrderSide.SELL, order_type=OrderType.LIMIT, limit_price=200.0)
    assert resolve_reference_price(above, _account(), quote_fn=lambda _s: 150.0) == 200.0


def test_resolve_reference_price_sell_limit_tiny_without_quote_is_none():
    order = _order(side=OrderSide.SELL, order_type=OrderType.LIMIT, limit_price=0.01)
    assert resolve_reference_price(order, _account(), quote_fn=lambda _s: None) is None


def test_resolve_reference_price_sell_limit_uses_position_mark():
    account = _account(
        positions=[
            Position(
                id="p1",
                symbol="AAPL",
                quantity=10,
                average_entry_price=140.0,
                current_price=148.5,
            )
        ]
    )
    order = _order(side=OrderSide.SELL, order_type=OrderType.LIMIT, limit_price=0.01)
    assert resolve_reference_price(account=account, order=order, quote_fn=lambda _s: None) == 148.5


def test_resolve_reference_price_sell_stop_limit_uses_max_of_limit_and_quote():
    order = _order(
        side=OrderSide.SELL,
        order_type=OrderType.STOP_LIMIT,
        limit_price=0.01,
        stop_price=140.0,
    )
    assert constraining_order_price(order) == 0.01
    assert resolve_reference_price(order, _account(), quote_fn=lambda _s: 150.0) == 150.0


def test_resolve_reference_price_buy_limit_keeps_tiny_limit():
    """Buy limit caps spend — do not lift a cheap bid to the quote."""
    order = _order(side=OrderSide.BUY, order_type=OrderType.LIMIT, limit_price=0.01)
    assert resolve_reference_price(order, _account(), quote_fn=lambda _s: 150.0) == 0.01


def test_sell_limit_tiny_limit_price_409_does_not_hit_broker(tmp_path):
    """qty=10000 × dummy $0.01 looks like $100; marketable sell is ~$1.5M."""
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=10000,
            limit_price=0.01,
            id="lim-tiny-sell",
            idempotency_key="lim-tiny-sell",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 409
    assert result.blocked is True
    assert result.gate in ("mandate", "risk")
    broker.submit_order.assert_not_called()
    detail = (result.detail or "").lower()
    assert "notional" in detail or "exceed" in detail or "risk" in detail


def test_sell_stop_limit_tiny_limit_price_409_does_not_hit_broker(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            side=OrderSide.SELL,
            order_type=OrderType.STOP_LIMIT,
            quantity=10000,
            limit_price=0.01,
            stop_price=140.0,
            id="sl-tiny-sell",
            idempotency_key="sl-tiny-sell",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 409
    assert result.blocked is True
    assert result.gate in ("mandate", "risk")
    broker.submit_order.assert_not_called()
    detail = (result.detail or "").lower()
    assert "notional" in detail or "exceed" in detail or "risk" in detail


def test_sell_limit_tiny_without_quote_does_not_approve(tmp_path):
    """No mark/quote: cannot certify sell notional from a tiny floor limit."""
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=10000,
            limit_price=0.01,
            id="lim-tiny-sell-nq",
            idempotency_key="lim-tiny-sell-nq",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: None,
    )
    assert result.http_status == 409
    assert result.detail == NO_CONSTRAINING_PRICE_DETAIL
    broker.submit_order.assert_not_called()


def test_sell_limit_honest_still_submits(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=1,
            limit_price=150.0,
            id="lim-honest-sell",
            idempotency_key="lim-honest-sell",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 200
    assert result.blocked is False
    broker.submit_order.assert_called_once()


def test_buy_limit_tiny_still_submits_spend_is_capped(tmp_path):
    """Buy limit $0.01 × 10000 = $100 — honest max spend, must not lift to quote."""
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10000,
            limit_price=0.01,
            id="lim-tiny-buy",
            idempotency_key="lim-tiny-buy",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 200
    assert result.blocked is False
    broker.submit_order.assert_called_once()


def test_sell_stop_limit_honest_still_submits(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            side=OrderSide.SELL,
            order_type=OrderType.STOP_LIMIT,
            quantity=1,
            limit_price=150.0,
            stop_price=140.0,
            id="sl-honest-sell",
            idempotency_key="sl-honest-sell",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 200
    assert result.blocked is False
    broker.submit_order.assert_called_once()


def test_market_zero_limit_without_quote_does_not_approve(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            order_type=OrderType.MARKET,
            quantity=10,
            limit_price=0.0,
            id="mkt-zero",
            idempotency_key="mkt-zero",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: None,
    )
    assert result.http_status == 409
    assert result.detail == NO_CONSTRAINING_PRICE_DETAIL
    broker.submit_order.assert_not_called()


def test_market_negative_limit_without_quote_does_not_approve(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            order_type=OrderType.MARKET,
            quantity=10,
            limit_price=-5.0,
            id="mkt-neg",
            idempotency_key="mkt-neg",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: None,
    )
    assert result.http_status == 409
    assert result.detail == NO_CONSTRAINING_PRICE_DETAIL
    broker.submit_order.assert_not_called()


def test_limit_price_zero_without_quote_does_not_approve(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            order_type=OrderType.LIMIT,
            quantity=10,
            limit_price=0.0,
            id="lim-zero",
            idempotency_key="lim-zero",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: None,
    )
    assert result.http_status == 409
    assert result.detail == NO_CONSTRAINING_PRICE_DETAIL
    broker.submit_order.assert_not_called()


def test_limit_price_zero_quote_does_not_constrain_limit_order(tmp_path):
    """A quote is not on LimitOrderRequest; limit<=0 must not approve."""
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            order_type=OrderType.LIMIT,
            quantity=1,
            limit_price=0.0,
            id="lim-zero-quote",
            idempotency_key="lim-zero-quote",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 409
    assert result.detail == NO_CONSTRAINING_PRICE_DETAIL
    broker.submit_order.assert_not_called()


def test_market_order_with_honest_quote_still_submits(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_desk_order(
        _order(
            order_type=OrderType.MARKET,
            quantity=1,
            limit_price=None,
            id="mkt-honest",
            idempotency_key="mkt-honest",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 200
    assert result.blocked is False
    broker.submit_order.assert_called_once()


def test_risk_reject_does_not_call_broker(tmp_path):
    path = _mandate_file(tmp_path, {**TIGHT, "max_notional_per_order": None})
    broker = _broker()
    store = InMemoryOrderStore()
    engine = RiskEngine(RiskLimits(max_order_notional=1_000.0))
    result = place_desk_order(
        _order(quantity=20, limit_price=200.0),  # $4,000 > $1,000
        broker=broker,
        store=store,
        risk_engine=engine,
        mandate_path=path,
    )
    assert result.blocked is True
    assert result.gate == "risk"
    assert result.http_status == 409
    assert "Risk Gate Rejected" in (result.detail or "")
    broker.submit_order.assert_not_called()


def test_happy_path_submits_via_order_manager(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    seen = {}
    inner = broker.submit_order.side_effect

    def _submit(order):
        seen["status"] = order.status
        return inner(order)

    broker.submit_order.side_effect = _submit
    store = InMemoryOrderStore()
    result = place_desk_order(
        _order(),
        broker=broker,
        store=store,
        mandate_path=path,
    )
    assert result.blocked is False
    assert result.http_status == 200
    assert result.journaled is True
    broker.submit_order.assert_called_once()
    assert seen["status"] == OrderStatus.RISK_APPROVED
    assert result.order.status == OrderStatus.SUBMITTED
    assert result.mandate_status == "loaded"
    rows = journal_store.load_raw_entries()
    assert any(row.get("strategy_id") == DESK_STRATEGY_ID for row in rows)


def test_journal_raise_after_accept_is_not_http_200(tmp_path, monkeypatch):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()

    def _boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _boom)
    result = place_desk_order(
        _order(),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
    )
    assert result.http_status != 200
    assert result.http_status == 502
    assert result.journaled is False
    assert result.detail["error"] == "journal_write_failed"
    assert result.detail["broker_submitted"] is True
    assert "not journaled" in result.detail["message"].lower()
    broker.submit_order.assert_called_once()


def test_journal_none_after_accept_is_not_http_200(tmp_path, monkeypatch):
    path = _mandate_file(tmp_path, TIGHT)
    monkeypatch.setattr(
        "backend.trading.engine.desk_orders.record_from_order", lambda *_a, **_k: None
    )
    result = place_desk_order(
        _order(),
        broker=_broker(),
        store=InMemoryOrderStore(),
        mandate_path=path,
    )
    assert result.http_status == 502
    assert result.journaled is False


def test_reject_journal_failure_is_visible(tmp_path, monkeypatch):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()

    def _boom(*_a, **_k):
        raise RuntimeError("journal down")

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _boom)
    result = place_desk_order(
        _order(symbol="GME", id="gme-j", idempotency_key="gme-j"),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
    )
    assert result.http_status == 500
    assert result.http_status != 200
    assert result.blocked is True
    assert result.journaled is False
    assert result.detail["journaled"] is False
    assert result.detail["broker_submitted"] is False
    assert "no durable audit" in result.detail["message"].lower()
    broker.submit_order.assert_not_called()


def test_missing_mandate_fail_closed(tmp_path):
    broker = _broker()
    store = InMemoryOrderStore()
    result = place_desk_order(
        _order(),
        broker=broker,
        store=store,
        mandate_path=tmp_path / "absent.json",
    )
    assert result.blocked is True
    assert result.gate == "mandate"
    assert result.mandate_status == "missing"
    assert "fail closed" in (result.detail or "").lower()
    broker.submit_order.assert_not_called()
    broker.get_account_summary.assert_not_called()


def test_corrupt_mandate_fail_closed(tmp_path):
    path = tmp_path / "mandate.json"
    path.write_text("{nope", encoding="utf-8")
    broker = _broker()
    result = place_desk_order(
        _order(),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
    )
    assert result.blocked is True
    assert result.mandate_status == "corrupt"
    broker.submit_order.assert_not_called()


def test_kill_buy_still_409(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    kill_switch.engage("unit test halt")
    broker = _broker()
    result = place_desk_order(
        _order(),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
    )
    assert result.blocked is True
    assert result.http_status == 409
    assert result.detail == KILL_BUY_DETAIL
    assert result.gate == "kill"
    broker.submit_order.assert_not_called()


def test_sell_skips_kill_d_sell_kill_backlog(tmp_path):
    """D-sell-kill: sell-without-kill is left as-is (not R1-SAFE)."""
    path = _mandate_file(tmp_path, TIGHT)
    kill_switch.engage("unit test halt")
    broker = _broker()
    result = place_desk_order(
        _order(side=OrderSide.SELL, id="sell-1", idempotency_key="sell-1"),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.blocked is False
    broker.submit_order.assert_called_once()


def test_ops_payload_matches_inspect_source(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    payload = ops_mandate_payload()
    # Default inspect may see repo file; pin via inspect of the same path.
    from backend.trading.engine.desk_orders import desk_mandate_snapshot

    snap = desk_mandate_snapshot(path)
    payload = ops_mandate_payload(snap)
    assert payload["mandate_status"] == "loaded"
    assert payload["mandate_path"] == str(path)
    assert payload["mandate"]["blocked_symbols"] == ["GME"]
    result = place_desk_order(
        _order(symbol="GME", id="gme-ops", idempotency_key="gme-ops"),
        broker=_broker(),
        store=InMemoryOrderStore(),
        mandate_path=path,
    )
    assert result.mandate_status == payload["mandate_status"]
    assert result.mandate_path == payload["mandate_path"]


def test_ops_missing_does_not_invent_loaded_mandate(tmp_path):
    from backend.trading.engine.desk_orders import desk_mandate_snapshot

    snap = desk_mandate_snapshot(tmp_path / "nope.json")
    payload = ops_mandate_payload(snap)
    assert payload["mandate_status"] == "missing"
    assert payload["mandate"] is None


def test_desk_orders_module_does_not_import_intent_or_signal_processor():
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "backend" / "trading" / "engine" / "desk_orders.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported.extend(f"{node.module}.{alias.name}" for alias in node.names)
    joined = " ".join(imported)
    assert "IntentStore" not in joined
    assert "signal_processor" not in joined
    assert "process_signal" not in joined
