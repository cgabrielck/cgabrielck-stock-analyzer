"""O5 fail-cases: Desk POST must not dual-execute around worker gates.

On 2026-10-22 audit HEAD, Desk ``POST /api/orders`` called the broker after
kill-only. Worker went Mandate → Risk → OrderManager (SignalProcessor).
This file would have been red then; it stays red if the shared gate or
the journal/audit stamp is removed.

Not in scope: IntentStore / H1–H3 / M8 Kelly sizer, flatten-through-kill,
D7-DESIGN, fake verified/live, lab advisory → OrderManager.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.trading.engine.desk_orders import (
    DESK_ACTOR,
    DESK_STRATEGY_ID,
    DESK_WRITE_POINT,
    desk_journal_has_path_stamp,
    desk_journal_provenance_extra,
    place_desk_order,
    reset_desk_mandate_gate,
)
from backend.trading.engine.order_manager import InMemoryOrderStore
from backend.trading.models import (
    AccountSummary,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from backend.trading.risk.gates import RiskEngine, RiskLimits
from backend.trading.safety import audit, kill_switch
from backend.trading.safety.mandate import MandateGate, TradingMandate
from backend.utils.provenance import journal_store


ROOT = Path(__file__).resolve().parents[3]
DESK_ORDERS = ROOT / "backend" / "trading" / "engine" / "desk_orders.py"
API_APP = ROOT / "backend" / "api" / "app.py"

TIGHT = {
    "allowed_symbols": ["AAPL", "MSFT"],
    "blocked_symbols": ["GME"],
    "max_notional_per_order": 10_000.0,
    "max_daily_orders": 50,
    "allowed_sides": ["buy", "sell"],
}


def _order(**kwargs):
    payload = dict(
        id="o5-1",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1,
        limit_price=150.0,
        idempotency_key="o5-1",
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
        order.id = "broker-o5"
        return order

    broker.submit_order.side_effect = _submit
    return broker


def _mandate_file(tmp_path, payload):
    path = tmp_path / "mandate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    journal = tmp_path / "trade_journal.json"
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH")
    reset_desk_mandate_gate()
    yield {"audit_dir": audit_dir, "journal": journal, "tmp_path": tmp_path}
    reset_desk_mandate_gate()


def _function_def(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name} is missing function {name}")


def _call_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
            if isinstance(func.value, ast.Name):
                names.add(f"{func.value.id}.{func.attr}")
    return names


def test_o5_place_desk_order_source_must_call_shared_gates():
    """Fail-case: deleting Mandate / Risk / OM / journal / actor turns this red."""
    src = DESK_ORDERS.read_text(encoding="utf-8")
    place = _function_def(DESK_ORDERS, "place_desk_order")
    journal = _function_def(DESK_ORDERS, "_journal_order")
    extra = _function_def(DESK_ORDERS, "desk_journal_provenance_extra")
    called = _call_names(place) | _call_names(journal)

    assert "kill_switch.is_halted" in called or "is_halted" in called
    assert "evaluate" in called
    assert "evaluate_order" in called
    assert "OrderManager" in called
    assert "submit_new_order" in called
    assert "record_from_order" in called
    assert "stamp_risk_approved" in called
    assert "log_mandate_decision" in called
    assert "log_risk_decision" in called
    assert "log_order_submission" in called

    assert src.count("actor=DESK_ACTOR") >= 3
    extra_src = ast.get_source_segment(src, extra) or ast.unparse(extra)
    assert "DESK_WRITE_POINT" in extra_src
    assert "DESK_ACTOR" in extra_src
    stamp = desk_journal_provenance_extra()
    assert stamp == {"write_point": DESK_WRITE_POINT, "actor": DESK_ACTOR}


def test_o5_fastapi_submit_order_must_call_place_desk_order_not_broker():
    """Fail-case: reverting POST /api/orders to _broker.submit_order turns this red."""
    src = API_APP.read_text(encoding="utf-8")
    submit = _function_def(API_APP, "submit_order")
    called = _call_names(submit)
    body = ast.get_source_segment(src, submit) or ast.unparse(submit)
    assert "place_desk_order" in called
    assert "place_desk_order(" in body
    assert "_broker.submit_order" not in body
    assert not any(name.endswith(".submit_order") for name in called)


def test_o5_happy_path_journals_actor_desk_and_audit_actor_desk(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    result = place_desk_order(
        _order(),
        broker=_broker(),
        store=InMemoryOrderStore(),
        mandate_path=path,
    )
    assert result.http_status == 200
    assert result.journaled is True
    rows = journal_store.load_raw_entries()
    stamped = rows[-1]
    assert desk_journal_has_path_stamp(stamped)
    assert stamped["strategy_id"] == DESK_STRATEGY_ID
    assert stamped["provenance"]["actor"] == DESK_ACTOR
    assert stamped["provenance"]["write_point"] == DESK_WRITE_POINT
    assert stamped["provenance"]["verified"] is False
    assert stamped["provenance"]["live"] is False

    events = audit.read_recent_events(audit_dir=tmp_path / "audit", limit=50)
    money = [
        e
        for e in events
        if e.get("event_type") in {"mandate_decision", "risk_decision", "order_submission"}
    ]
    kinds = {e.get("event_type") for e in money}
    assert "mandate_decision" in kinds
    assert "risk_decision" in kinds
    assert "order_submission" in kinds
    assert all(e.get("actor") == DESK_ACTOR for e in money)


def test_o5_mandate_blocked_symbol_same_as_worker_gate(tmp_path):
    """Worker MandateGate.evaluate(GME) refuses; Desk place must refuse too."""
    path = _mandate_file(tmp_path, TIGHT)
    mandate = TradingMandate(**TIGHT)
    worker_gate = MandateGate(mandate)
    order = _order(symbol="GME", id="o5-gme", idempotency_key="o5-gme")
    worker = worker_gate.evaluate(order, 20.0)
    assert worker.approved is False

    broker = _broker()
    result = place_desk_order(
        order,
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
    )
    assert result.http_status == 409
    assert result.blocked is True
    assert result.gate == "mandate"
    broker.submit_order.assert_not_called()
    assert desk_journal_has_path_stamp(journal_store.load_raw_entries()[-1])


def test_o5_risk_notional_same_kernel_as_worker(tmp_path):
    """Same RiskEngine instance/limits: worker refuse ⇒ Desk 409, no broker."""
    path = _mandate_file(tmp_path, {**TIGHT, "max_notional_per_order": None})
    engine = RiskEngine(RiskLimits(max_order_notional=1_000.0))
    order = _order(quantity=20, limit_price=200.0)  # $4,000
    worker = engine.evaluate_order(order, _account())
    assert worker.approved is False
    assert "exceeds limit" in (worker.reason or "")

    broker = _broker()
    result = place_desk_order(
        order,
        broker=broker,
        store=InMemoryOrderStore(),
        risk_engine=engine,
        mandate_path=path,
    )
    assert result.http_status == 409
    assert result.gate == "risk"
    assert "Risk Gate Rejected" in (result.detail or "")
    broker.submit_order.assert_not_called()


def test_o5_risk_buying_power_desk_cannot_succeed_when_worker_refuses(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    engine = RiskEngine()
    account = _account(buying_power=50.0)
    order = _order(quantity=1, limit_price=150.0)
    worker = engine.evaluate_order(order, account)
    assert worker.approved is False
    assert "buying power" in (worker.reason or "").lower()

    broker = _broker(account)
    result = place_desk_order(
        order,
        broker=broker,
        store=InMemoryOrderStore(),
        risk_engine=engine,
        mandate_path=path,
        account=account,
    )
    assert result.http_status == 409
    assert result.gate == "risk"
    broker.submit_order.assert_not_called()


def test_o5_risk_concentration_desk_cannot_succeed_when_worker_refuses(tmp_path):
    path = _mandate_file(tmp_path, {**TIGHT, "max_notional_per_order": None})
    engine = RiskEngine(RiskLimits(max_order_notional=20_000.0, max_position_concentration=0.25))
    account = _account(
        positions=[Position(id="1", symbol="AAPL", quantity=100, average_entry_price=150.0)]
    )
    order = _order(quantity=100, limit_price=110.0)  # +$11k on $15k → 26%
    worker = engine.evaluate_order(order, account)
    assert worker.approved is False
    assert "concentration" in (worker.reason or "").lower()

    broker = _broker(account)
    result = place_desk_order(
        order,
        broker=broker,
        store=InMemoryOrderStore(),
        risk_engine=engine,
        mandate_path=path,
        account=account,
    )
    assert result.http_status == 409
    assert result.gate == "risk"
    broker.submit_order.assert_not_called()


def test_o5_risk_zero_portfolio_desk_cannot_succeed_when_worker_refuses(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    engine = RiskEngine()
    account = _account(portfolio_value=0.0, buying_power=0.0, cash=0.0)
    order = _order()
    worker = engine.evaluate_order(order, account)
    assert worker.approved is False

    broker = _broker(account)
    result = place_desk_order(
        order,
        broker=broker,
        store=InMemoryOrderStore(),
        risk_engine=engine,
        mandate_path=path,
        account=account,
    )
    assert result.http_status == 409
    assert result.gate == "risk"
    broker.submit_order.assert_not_called()


def test_o5_kill_buy_same_shared_sentinel(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    kill_switch.engage("o5")
    broker = _broker()
    result = place_desk_order(
        _order(),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
    )
    assert result.http_status == 409
    assert result.gate == "kill"
    broker.submit_order.assert_not_called()
    events = audit.read_recent_events(audit_dir=tmp_path / "audit", limit=20)
    kill_events = [e for e in events if e.get("event_type") == "kill_switch_check"]
    assert kill_events
    assert all(e.get("actor") == DESK_ACTOR for e in kill_events)


def test_o5_removing_audit_actor_kw_is_visible_in_source():
    """Fail-case: audit.log_* on this path must pass actor=DESK_ACTOR, not default worker."""
    src = DESK_ORDERS.read_text(encoding="utf-8")
    place = _function_def(DESK_ORDERS, "place_desk_order")
    body = ast.get_source_segment(src, place) or ast.unparse(place)
    assert "actor=DESK_ACTOR" in body
    assert body.count("actor=DESK_ACTOR") >= 3
    defaults = audit.log_event.__kwdefaults__ or {}
    assert defaults.get("actor") == "worker"
