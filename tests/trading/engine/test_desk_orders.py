"""R1-SAFE Desk place-order gates (not IntentStore / SignalProcessor)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from backend.trading.engine.desk_orders import (
    DESK_STRATEGY_ID,
    KILL_BUY_DETAIL,
    ops_mandate_payload,
    place_desk_order,
    reset_desk_mandate_gate,
)
from backend.trading.engine.order_manager import InMemoryOrderStore
from backend.trading.models import AccountSummary, Order, OrderSide, OrderStatus, OrderType
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
    store = InMemoryOrderStore()
    result = place_desk_order(
        _order(),
        broker=broker,
        store=store,
        mandate_path=path,
    )
    assert result.blocked is False
    assert result.http_status == 200
    broker.submit_order.assert_called_once()
    assert result.order.status == OrderStatus.SUBMITTED
    assert result.mandate_status == "loaded"
    rows = journal_store.load_raw_entries()
    assert any(row.get("strategy_id") == DESK_STRATEGY_ID for row in rows)


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
    from pathlib import Path

    text = Path(__file__).resolve().parents[3] / "backend" / "trading" / "engine" / "desk_orders.py"
    src = text.read_text(encoding="utf-8")
    assert "IntentStore" not in src
    assert "from backend.trading.engine.signal_processor" not in src
    assert "process_signal" not in src
