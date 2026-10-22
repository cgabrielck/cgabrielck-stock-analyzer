"""Stamp-APPROVED-without-evaluate contract (issue #10 STAMP parent).

``RISK_APPROVED`` is a claim that the authorization/risk evaluates this
path owns actually ran and approved. It is not a synonym for "about to
submit", "shadow needs this status", or "operator clicked sell".

What MAY be stamped ``RISK_APPROVED``
-------------------------------------
An order for which ``RiskEngine.evaluate_order`` was invoked and returned
``approved=True``.

On worker / Desk money paths that attach a ``MandateGate`` (worker
``_submit_sell``, worker buy, Desk ``place_desk_order``),
``MandateGate.evaluate`` must also have been invoked and returned
``approved=True`` *before* this stamp.

``SignalProcessor`` runs Risk only; its callers (worker buy/sell) must
evaluate Mandate first. ``OrderManager`` must not stamp (O7).

What must NOT be stamped ``RISK_APPROVED``
------------------------------------------
- Skipped evaluate (``None`` decision, never called, missing account,
  missing positive reference price).
- A 0.995 / dummy / tiny limit used as the only notional so a cap that
  would fail at the honest mark silently passes.
- Shadow/paper convenience before submit.
- Streamlit lab dashboard (S3) — closed via ``place_desk_order`` /
  ``place_lab_manual_order`` (``account is None`` → reject, no stamp).
- Intent / M8 / R1-PATH — remains OPEN; not this module.
- Worker flatten-through-kill — remains OPEN; Desk POST kill-on-sell
  is ``place_desk_order``, not this module.

Failure is ``REJECTED`` (or never submitted). Never invent ``FILLED``,
verified, or live from a skipped evaluate.
"""
from __future__ import annotations

from typing import Optional

from backend.trading.models import Order, OrderStatus
from backend.trading.risk.gates import RiskDecision
from backend.trading.safety.mandate import MandateDecision

VENUE_STATUSES = frozenset(
    {
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
    }
)


class StampWithoutEvaluateError(ValueError):
    """RISK_APPROVED was requested without a real Mandate/Risk evaluate."""


def may_stamp_risk_approved(
    *,
    risk_decision: Optional[RiskDecision],
    mandate_decision: Optional[MandateDecision] = None,
    require_mandate: bool = False,
) -> bool:
    """True only when the required evaluates ran and approved."""
    if risk_decision is None or not risk_decision.approved:
        return False
    if require_mandate and (mandate_decision is None or not mandate_decision.approved):
        return False
    return True


def stamp_risk_approved(
    order: Order,
    *,
    risk_decision: Optional[RiskDecision],
    mandate_decision: Optional[MandateDecision] = None,
    require_mandate: bool = False,
) -> Order:
    """Stamp ``RISK_APPROVED`` only after real evaluate-and-approve.

    ``risk_decision is None`` or ``mandate_decision is None`` (when
    required) means evaluate did not run — that is not an approve.
    """
    if risk_decision is None:
        raise StampWithoutEvaluateError(
            "Cannot stamp RISK_APPROVED: RiskEngine was not evaluated."
        )
    if not risk_decision.approved:
        raise StampWithoutEvaluateError(
            "Cannot stamp RISK_APPROVED: RiskEngine did not approve"
            f"{f' ({risk_decision.reason})' if risk_decision.reason else ''}."
        )
    if require_mandate:
        if mandate_decision is None:
            raise StampWithoutEvaluateError(
                "Cannot stamp RISK_APPROVED: Mandate was not evaluated."
            )
        if not mandate_decision.approved:
            raise StampWithoutEvaluateError(
                "Cannot stamp RISK_APPROVED: Mandate did not approve"
                f"{f' ({mandate_decision.reason})' if mandate_decision.reason else ''}."
            )
    order.status = OrderStatus.RISK_APPROVED
    return order


def sell_reached_venue(order: Optional[Order]) -> bool:
    """True only after a real submit/fill — not RISK_APPROVED or REJECTED."""
    if order is None:
        return False
    status = order.status
    return status in VENUE_STATUSES
