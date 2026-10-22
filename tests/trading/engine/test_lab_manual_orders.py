"""Streamlit / lab manual surface: S3 + Mandate/Risk/journal honesty.

These cases would have succeeded on main @ fddc5cb (dashboard stamped
RISK_APPROVED / FILLED when summary was None). After this PR they reject.
Not verified / live.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.trading.engine.desk_orders import (
    ACCOUNT_MISSING_DETAIL,
    SHADOW_NO_POSITIVE_FILL_DETAIL,
    place_desk_order,
    place_lab_manual_order,
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
from backend.trading.risk.gates import positive_price
from backend.trading.safety import audit, kill_switch
from backend.utils.provenance import journal_store


TIGHT = {
    "allowed_symbols": ["AAPL", "MSFT"],
    "blocked_symbols": ["GME"],
    "max_notional_per_order": 10_000.0,
    "max_daily_orders": 50,
    "allowed_sides": ["buy", "sell"],
}


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
        order.id = "broker-lab-1"
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
    yield {"audit_dir": audit_dir, "journal": journal}
    reset_desk_mandate_gate()


def test_s3_shadow_none_summary_rejects_not_filled(tmp_path):
    """S3: summary is None → REJECTED, never RISK_APPROVED / FILLED."""
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    store = InMemoryOrderStore()
    result = place_lab_manual_order(
        broker=broker,
        store=store,
        symbol="AAPL",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=150.0,
        account=None,
        execution_mode="shadow",
        mandate_path=path,
    )
    assert result.blocked is True
    assert result.http_status == 409
    assert result.gate == "risk"
    assert result.detail == ACCOUNT_MISSING_DETAIL
    assert result.order.status == OrderStatus.REJECTED
    assert result.order.status != OrderStatus.RISK_APPROVED
    assert result.order.status != OrderStatus.FILLED
    broker.submit_order.assert_not_called()
    rows = journal_store.load_raw_entries()
    assert rows, "S3 reject must be journaled"
    assert any("AAPL" in str(row.get("symbol")) for row in rows)


def test_s3_paper_none_summary_rejects_not_submitted(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_lab_manual_order(
        broker=broker,
        store=InMemoryOrderStore(),
        symbol="AAPL",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=150.0,
        account=None,
        execution_mode="paper",
        mandate_path=path,
    )
    assert result.blocked is True
    assert result.order.status == OrderStatus.REJECTED
    broker.submit_order.assert_not_called()


def test_place_desk_order_explicit_none_account_rejects(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    order = Order(
        id="s3-desk",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=1,
        limit_price=150.0,
        idempotency_key="s3-desk",
    )
    result = place_desk_order(
        order,
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        account=None,
        execution_mode="shadow",
    )
    assert result.blocked is True
    assert result.detail == ACCOUNT_MISSING_DETAIL
    assert result.order.status == OrderStatus.REJECTED
    broker.submit_order.assert_not_called()
    broker.get_account_summary.assert_not_called()


def test_streamlit_shadow_mandate_block_is_rejected_not_filled(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_lab_manual_order(
        broker=broker,
        store=InMemoryOrderStore(),
        symbol="GME",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=20.0,
        account=_account(),
        execution_mode="shadow",
        mandate_path=path,
    )
    assert result.blocked is True
    assert result.gate == "mandate"
    assert result.http_status == 409
    assert result.order.status == OrderStatus.REJECTED
    assert result.order.status != OrderStatus.FILLED
    broker.submit_order.assert_not_called()


def test_streamlit_paper_mandate_block_does_not_call_broker(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_lab_manual_order(
        broker=broker,
        store=InMemoryOrderStore(),
        symbol="GME",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=20.0,
        account=_account(),
        execution_mode="paper",
        mandate_path=path,
    )
    assert result.blocked is True
    assert result.gate == "mandate"
    broker.submit_order.assert_not_called()


def test_streamlit_shadow_risk_reject_not_filled(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_lab_manual_order(
        broker=broker,
        store=InMemoryOrderStore(),
        symbol="AAPL",
        side="buy",
        quantity=1000,
        order_type="limit",
        limit_price=150.0,
        account=_account(),
        execution_mode="shadow",
        mandate_path=path,
    )
    assert result.blocked is True
    assert result.gate in ("mandate", "risk")
    assert result.order.status == OrderStatus.REJECTED
    assert result.order.status != OrderStatus.FILLED
    broker.submit_order.assert_not_called()


def test_streamlit_shadow_honest_path_evaluates_then_fills_not_broker(tmp_path):
    """Lab shadow fill after Mandate+Risk — not verified / live."""
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    store = InMemoryOrderStore()
    result = place_lab_manual_order(
        broker=broker,
        store=store,
        symbol="AAPL",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=150.0,
        account=_account(),
        execution_mode="shadow",
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.blocked is False
    assert result.http_status == 200
    assert result.journaled is True
    assert result.order.status == OrderStatus.FILLED
    broker.submit_order.assert_not_called()
    rows = journal_store.load_raw_entries()
    assert rows


def test_streamlit_paper_honest_path_still_submits(tmp_path):
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    result = place_lab_manual_order(
        broker=broker,
        store=InMemoryOrderStore(),
        symbol="AAPL",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=150.0,
        account=_account(),
        execution_mode="paper",
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status == 200
    assert result.journaled is True
    broker.submit_order.assert_called_once()


def test_streamlit_shadow_journal_fail_is_not_success(tmp_path, monkeypatch):
    path = _mandate_file(tmp_path, TIGHT)

    def _boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("backend.trading.engine.desk_orders.record_from_order", _boom)
    broker = _broker()
    result = place_lab_manual_order(
        broker=broker,
        store=InMemoryOrderStore(),
        symbol="AAPL",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=150.0,
        account=_account(),
        execution_mode="shadow",
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.http_status != 200
    assert result.http_status == 502
    assert result.journaled is False
    assert result.detail["broker_submitted"] is False
    broker.submit_order.assert_not_called()


def test_market_shadow_with_quote_is_not_filled_at_zero(tmp_path):
    """Would have been HTTP 200 FILLED @ $0 on 82dabda — never a fill."""
    path = _mandate_file(tmp_path, TIGHT)
    broker = _broker()
    store = InMemoryOrderStore()
    result = place_lab_manual_order(
        broker=broker,
        store=store,
        symbol="AAPL",
        side="buy",
        quantity=1,
        order_type="market",
        limit_price=None,
        account=_account(),
        execution_mode="shadow",
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.blocked is True
    assert result.http_status == 409
    assert result.gate == "shadow"
    assert result.detail == SHADOW_NO_POSITIVE_FILL_DETAIL
    assert result.order.status == OrderStatus.REJECTED
    assert result.order.status != OrderStatus.FILLED
    assert result.order.status != OrderStatus.RISK_APPROVED
    assert positive_price(getattr(result.order, "filled_avg_price", None)) is None
    broker.submit_order.assert_not_called()
    saved = store.get_order(result.order.id)
    if saved is not None:
        assert saved.status != OrderStatus.FILLED


def test_market_shadow_sell_with_position_mark_is_not_filled_at_zero(tmp_path):
    """Position current_price=150, no quote_fn — still not FILLED @ $0."""
    path = _mandate_file(tmp_path, TIGHT)
    account = _account(
        positions=[
            Position(
                id="p1",
                symbol="AAPL",
                quantity=10,
                average_entry_price=100.0,
                current_price=150.0,
            )
        ]
    )
    broker = _broker(account)
    store = InMemoryOrderStore()
    result = place_lab_manual_order(
        broker=broker,
        store=store,
        symbol="AAPL",
        side="sell",
        quantity=1,
        order_type="market",
        limit_price=None,
        account=account,
        execution_mode="shadow",
        mandate_path=path,
    )
    assert result.blocked is True
    assert result.http_status == 409
    assert result.order.status == OrderStatus.REJECTED
    assert result.order.status != OrderStatus.FILLED
    assert positive_price(getattr(result.order, "filled_avg_price", None)) is None
    broker.submit_order.assert_not_called()


def test_dashboard_source_no_longer_stamps_without_evaluate():
    src = Path(__file__).resolve().parents[3] / "backend" / "trading" / "ui" / "dashboard.py"
    text = src.read_text(encoding="utf-8")
    assert "place_lab_manual_order" in text
    assert "o.status = OrderStatus.RISK_APPROVED" not in text
    assert "evaluate_order(o, summary) if summary else None" not in text
    assert "SignalProcessor" not in text
    assert "ShadowTradingEngine" not in text
