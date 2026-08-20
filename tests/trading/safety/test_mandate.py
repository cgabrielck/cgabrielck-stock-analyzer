"""Tests for mandate.py — order authorization and daily limits."""
import pytest
from backend.trading.safety.mandate import MandateGate, TradingMandate
from backend.trading.models import Order, OrderSide, OrderType


def make_order(symbol="AAPL", side=OrderSide.BUY, quantity=10, limit_price=150.0, oid="test"):
    """Build a minimal valid Order (extra fields default via pydantic)."""
    return Order(
        id=oid,
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        limit_price=limit_price,
        idempotency_key=oid,
    )


@pytest.fixture
def gate():
    """Fresh MandateGate with a 10-order/day, $50K-per-order mandate."""
    mandate = TradingMandate(
        max_daily_orders=10,
        max_notional_per_order=50_000.0,
    )
    return MandateGate(mandate=mandate)


def test_approve_within_limits(gate):
    """Orders within daily and per-order limits are approved."""
    decision = gate.evaluate(make_order(quantity=10, limit_price=150.0), ref_price=150.0)
    assert decision.approved


def test_reject_excessive_notional(gate):
    """Block orders exceeding max_notional_per_order."""
    # 1000 × $1000 = $1M (exceeds $50K)
    order = make_order(symbol="TSLA", quantity=1000, limit_price=1000.0, oid="big")
    decision = gate.evaluate(order, ref_price=1000.0)
    assert not decision.approved
    assert "notional" in (decision.reason or "").lower()


def test_reject_after_daily_limit(gate):
    """Block new BUY orders once the daily limit is hit."""
    for _ in range(10):
        gate.record_order()
    decision = gate.evaluate(make_order(quantity=1, limit_price=100.0), ref_price=100.0)
    assert not decision.approved
    assert "daily order limit" in (decision.reason or "").lower()


def test_sell_orders_bypass_limits(gate):
    """SELL orders (exits) are always approved, even past the daily cap."""
    for _ in range(10):
        gate.record_order()
    order = make_order(symbol="AAPL", side=OrderSide.SELL, quantity=100,
                       limit_price=200.0, oid="exit")
    decision = gate.evaluate(order, ref_price=200.0)
    assert decision.approved


def test_blocked_symbol_denied():
    """Symbols on the block-list are hard-denied."""
    mandate = TradingMandate(blocked_symbols=["GME"])
    gate = MandateGate(mandate=mandate)
    decision = gate.evaluate(make_order(symbol="GME"), ref_price=20.0)
    assert not decision.approved
    assert "block" in (decision.reason or "").lower()


def test_allow_list_enforced():
    """If an allow-list is set, symbols not on it are denied."""
    mandate = TradingMandate(allowed_symbols=["AAPL", "MSFT"])
    gate = MandateGate(mandate=mandate)
    assert gate.evaluate(make_order(symbol="AAPL"), ref_price=150.0).approved
    denied = gate.evaluate(make_order(symbol="NVDA"), ref_price=150.0)
    assert not denied.approved
    assert "allow-list" in (denied.reason or "").lower()


def test_forbidden_side_denied():
    """A mandate that only allows 'sell' rejects BUY orders."""
    mandate = TradingMandate(allowed_sides=["sell"])
    gate = MandateGate(mandate=mandate)
    decision = gate.evaluate(make_order(side=OrderSide.BUY), ref_price=150.0)
    assert not decision.approved


def test_record_order_increments_counter(gate):
    """record_order() increments the daily count."""
    assert gate.orders_today == 0
    gate.record_order()
    gate.record_order()
    assert gate.orders_today == 2


def test_permissive_mandate_allows_everything():
    """The default permissive mandate constrains nothing."""
    gate = MandateGate(mandate=TradingMandate.permissive())
    for _ in range(100):
        gate.record_order()
    order = make_order(symbol="BRK.A", quantity=1, limit_price=500_000.0, oid="whale")
    assert gate.evaluate(order, ref_price=500_000.0).approved


def test_ref_price_falls_back_to_limit_price():
    """When ref_price is None, notional uses the order's limit_price."""
    mandate = TradingMandate(max_notional_per_order=1_000.0)
    gate = MandateGate(mandate=mandate)
    # 100 × $50 = $5000 > $1000, using limit_price since ref_price is None
    order = make_order(quantity=100, limit_price=50.0)
    decision = gate.evaluate(order, ref_price=None)
    assert not decision.approved
