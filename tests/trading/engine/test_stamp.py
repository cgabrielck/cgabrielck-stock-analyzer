"""STAMP parent contract: RISK_APPROVED only after evaluate."""
from __future__ import annotations

import pytest

from backend.trading.engine.stamp import (
    StampWithoutEvaluateError,
    may_stamp_risk_approved,
    sell_reached_venue,
    stamp_risk_approved,
)
from backend.trading.models import Order, OrderSide, OrderStatus, OrderType
from backend.trading.risk.gates import RiskDecision
from backend.trading.safety.mandate import MandateDecision


def _order():
    return Order(
        id="stamp-1",
        symbol="AAPL",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=10,
        limit_price=100.0,
        idempotency_key="stamp-1",
    )


def test_may_not_stamp_when_risk_not_evaluated():
    assert may_stamp_risk_approved(risk_decision=None) is False
    order = _order()
    with pytest.raises(StampWithoutEvaluateError, match="RiskEngine was not evaluated"):
        stamp_risk_approved(order, risk_decision=None)
    assert order.status == OrderStatus.DRAFT


def test_may_not_stamp_when_risk_rejected():
    decision = RiskDecision(approved=False, reason="too large")
    assert may_stamp_risk_approved(risk_decision=decision) is False
    order = _order()
    with pytest.raises(StampWithoutEvaluateError, match="did not approve"):
        stamp_risk_approved(order, risk_decision=decision)
    assert order.status == OrderStatus.DRAFT


def test_worker_sell_require_mandate_blocks_skipped_mandate():
    risk = RiskDecision(approved=True)
    assert (
        may_stamp_risk_approved(
            risk_decision=risk, mandate_decision=None, require_mandate=True
        )
        is False
    )
    order = _order()
    with pytest.raises(StampWithoutEvaluateError, match="Mandate was not evaluated"):
        stamp_risk_approved(
            order,
            risk_decision=risk,
            mandate_decision=None,
            require_mandate=True,
        )
    assert order.status == OrderStatus.DRAFT


def test_worker_sell_require_mandate_blocks_mandate_reject():
    order = _order()
    with pytest.raises(StampWithoutEvaluateError, match="Mandate did not approve"):
        stamp_risk_approved(
            order,
            risk_decision=RiskDecision(approved=True),
            mandate_decision=MandateDecision(approved=False, reason="sell forbidden"),
            require_mandate=True,
        )
    assert order.status == OrderStatus.DRAFT


def test_stamp_after_both_evaluates_approved():
    order = _order()
    stamp_risk_approved(
        order,
        risk_decision=RiskDecision(approved=True),
        mandate_decision=MandateDecision(approved=True),
        require_mandate=True,
    )
    assert order.status == OrderStatus.RISK_APPROVED


def test_signal_processor_path_stamps_after_risk_only():
    """SP does not run Mandate; require_mandate=False is the documented split."""
    order = _order()
    stamp_risk_approved(
        order,
        risk_decision=RiskDecision(approved=True),
        require_mandate=False,
    )
    assert order.status == OrderStatus.RISK_APPROVED


def test_sell_reached_venue_is_not_risk_approved_or_rejected():
    approved = _order()
    approved.status = OrderStatus.RISK_APPROVED
    rejected = _order()
    rejected.status = OrderStatus.REJECTED
    submitted = _order()
    submitted.status = OrderStatus.SUBMITTED
    filled = _order()
    filled.status = OrderStatus.FILLED
    assert sell_reached_venue(None) is False
    assert sell_reached_venue(approved) is False
    assert sell_reached_venue(rejected) is False
    assert sell_reached_venue(submitted) is True
    assert sell_reached_venue(filled) is True
