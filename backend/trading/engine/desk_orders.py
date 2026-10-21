"""Desk manual place-order path (R1-SAFE).

Manual Desk stays a separate path from IntentStore / H1 / SignalProcessor
(R1-PATH, DESIGN — not wired here). This module still enforces, in order:

    kill (buy-only, existing R6) → Mandate (fail-closed) → RiskEngine
    → OrderManager + journal/audit

so accepted and rejected Desk orders are traceable. ``/api/ops`` and this
path share ``inspect_mandate()`` — the UI must not show a mandate the order
path does not use.

TODO(D-sell-kill): Desk sells currently skip the kill switch, matching worker
buy-only halt. Suggested HARD control-plane item — not in the R1-SAFE buy list.

TODO(R4): max_daily_orders is process-local on this gate. Desk and worker do
not yet share a durable daily counter.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from backend.trading.broker import BrokerAdapter
from backend.trading.engine.order_manager import OrderManager, OrderStore
from backend.trading.models import AccountSummary, Order, OrderSide, OrderStatus
from backend.trading.risk.gates import RiskEngine
from backend.trading.safety import audit, kill_switch
from backend.trading.safety.mandate import (
    MANDATE_STATUS_CORRUPT,
    MANDATE_STATUS_MISSING,
    MandateGate,
    MandateSnapshot,
    inspect_mandate,
)
from backend.utils.provenance.journal_store import record_from_order

logger = logging.getLogger(__name__)

DESK_ACTOR = "desk"
DESK_STRATEGY_ID = "desk_manual"
KILL_BUY_DETAIL = "Kill switch is on — new buys blocked."

_gate_lock = threading.Lock()
_desk_gate: Optional[MandateGate] = None
_default_risk_engine = RiskEngine()


@dataclass
class DeskPlaceResult:
    order: Order
    blocked: bool
    http_status: int = 200
    detail: Any = None
    gate: Optional[str] = None
    mandate_status: Optional[str] = None
    mandate_path: Optional[str] = None
    audit_id: Optional[str] = None
    journaled: bool = False


def default_quote_price(symbol: str) -> Optional[float]:
    """Best-effort last price for market-order notional checks. Fail closed if None."""
    try:
        import yfinance as yf

        from backend.utils.price_utils import get_latest_price

        price, _session = get_latest_price(yf.Ticker(symbol))
        if price is None:
            return None
        value = float(price)
        return value if value > 0 else None
    except Exception:
        logger.debug("desk quote failed for %s", symbol, exc_info=True)
        return None


def resolve_reference_price(
    order: Order,
    account: Optional[AccountSummary] = None,
    quote_fn: Optional[Callable[[str], Optional[float]]] = None,
) -> Optional[float]:
    """Limit price, then mark on an existing position, then an optional quote."""
    if order.limit_price is not None:
        try:
            px = float(order.limit_price)
            if px > 0:
                return px
        except (TypeError, ValueError):
            pass
    symbol = str(order.symbol or "").upper()
    if account is not None:
        for pos in account.positions or []:
            if str(getattr(pos, "symbol", "") or "").upper() != symbol:
                continue
            mark = getattr(pos, "current_price", None)
            if mark in (None, ""):
                continue
            try:
                px = float(mark)
            except (TypeError, ValueError):
                continue
            if px > 0:
                return px
    fn = quote_fn or default_quote_price
    try:
        quoted = fn(symbol)
        if quoted is not None and float(quoted) > 0:
            return float(quoted)
    except Exception:
        logger.debug("desk quote_fn failed for %s", symbol, exc_info=True)
    return None


def desk_mandate_snapshot(path: Any = None) -> MandateSnapshot:
    """Same mandate source as ``GET /api/ops``."""
    return inspect_mandate(path)


def ops_mandate_payload(snapshot: Optional[MandateSnapshot] = None) -> dict:
    """Honest ops fields: never dump a fake permissive mandate as loaded."""
    snap = snapshot or inspect_mandate()
    mandate = None
    if snap.mandate is not None:
        dump = getattr(snap.mandate, "model_dump", None)
        mandate = dump() if callable(dump) else snap.mandate
    return {
        "mandate": mandate,
        "mandate_status": snap.status,
        "mandate_path": snap.path,
        "mandate_error": snap.error,
    }


def _gate_for_snapshot(snapshot: MandateSnapshot) -> MandateGate:
    """Reuse one process-local gate so Desk daily counts accumulate.

    TODO(R4): persist and share this counter with the worker.
    """
    global _desk_gate
    mandate = snapshot.mandate
    if mandate is None:
        return MandateGate()
    with _gate_lock:
        if _desk_gate is None:
            _desk_gate = MandateGate(mandate)
        else:
            _desk_gate.mandate = mandate
        return _desk_gate


def reset_desk_mandate_gate() -> None:
    """Test helper — drop the process-local daily counter."""
    global _desk_gate
    with _gate_lock:
        _desk_gate = None


def _side_value(order: Order) -> str:
    side = order.side.value if isinstance(order.side, OrderSide) else str(order.side)
    return side.lower()


def _journal_order(order: Order, *, audit_id: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Write a TradeLogEntry. None or raise = not journaled (never swallow)."""
    try:
        entry = record_from_order(order, audit_id=audit_id, strategy_id=DESK_STRATEGY_ID)
    except Exception as exc:
        logger.exception("Desk journal record failed for %s", order.symbol)
        return False, f"journal: {exc}"
    if not entry:
        return False, "journal: record_from_order returned no entry"
    return True, None


def _persist_trace(
    order: Order,
    store: Optional[OrderStore],
    *,
    audit_id: Optional[str],
) -> Tuple[bool, Optional[str]]:
    """Persist reject/accept trace. Failures are returned, not swallowed."""
    errors = []
    if store is not None:
        try:
            store.save_order(order)
        except Exception as exc:
            logger.exception("Desk order-store save failed for %s", order.symbol)
            errors.append(f"order_store: {exc}")
    journaled, journal_err = _journal_order(order, audit_id=audit_id)
    if not journaled and journal_err:
        errors.append(journal_err)
    if errors:
        return False, "; ".join(errors)
    return True, None


def _reject_not_persisted_detail(reason: str, persist_err: str, gate: str) -> Dict[str, Any]:
    return {
        "error": "reject_not_journaled",
        "message": (
            f"Order was not submitted ({reason}). "
            f"Reject was not persisted: {persist_err}. "
            "No durable audit is claimed."
        ),
        "journaled": False,
        "broker_submitted": False,
        "gate": gate,
        "reason": reason,
    }


def _journal_failed_after_broker_detail(order: Order, persist_err: str) -> Dict[str, Any]:
    order_id = getattr(order, "id", None)
    return {
        "error": "journal_write_failed",
        "message": (
            f"Order may exist at the broker but was not journaled"
            f"{f' (id={order_id})' if order_id else ''}. "
            f"{persist_err}. Desk does not claim a durable audit trail."
        ),
        "journaled": False,
        "broker_submitted": True,
        "order_id": order_id,
        "symbol": getattr(order, "symbol", None),
    }


def _block(
    order: Order,
    *,
    store: Optional[OrderStore],
    http_status: int,
    detail: str,
    gate: str,
    audit_id: Optional[str] = None,
    mandate_status: Optional[str] = None,
    mandate_path: Optional[str] = None,
) -> DeskPlaceResult:
    order.status = OrderStatus.REJECTED
    order.error_message = detail
    order.failed_at = datetime.now(timezone.utc)
    persisted, persist_err = _persist_trace(order, store, audit_id=audit_id)
    if not persisted:
        return DeskPlaceResult(
            order=order,
            blocked=True,
            http_status=500,
            detail=_reject_not_persisted_detail(detail, persist_err or "unknown persist error", gate),
            gate=gate,
            mandate_status=mandate_status,
            mandate_path=mandate_path,
            audit_id=audit_id,
            journaled=False,
        )
    return DeskPlaceResult(
        order=order,
        blocked=True,
        http_status=http_status,
        detail=detail,
        gate=gate,
        mandate_status=mandate_status,
        mandate_path=mandate_path,
        audit_id=audit_id,
        journaled=True,
    )


def place_desk_order(
    order: Order,
    *,
    broker: BrokerAdapter,
    store: OrderStore,
    risk_engine: Optional[RiskEngine] = None,
    quote_fn: Optional[Callable[[str], Optional[float]]] = None,
    mandate_path: Any = None,
) -> DeskPlaceResult:
    """Evaluate a manual Desk order, then submit via OrderManager.

    Does not call SignalProcessor / IntentStore. User-supplied quantity is
    kept (no Kelly resize) but still has to pass Mandate + RiskEngine.
    """
    engine = risk_engine or _default_risk_engine
    side = _side_value(order)
    snapshot = inspect_mandate(mandate_path)

    # 1. Kill — buy-only, existing R6 behaviour / message.
    # TODO(D-sell-kill): sells skip this check; leave as-is (not R1-SAFE).
    if kill_switch.is_halted() and side == "buy":
        audit_id = audit.log_event(
            "kill_switch_check",
            {
                "halted": True,
                "reason": kill_switch.get_reason() or KILL_BUY_DETAIL,
                "symbol": order.symbol,
                "side": side,
            },
            actor=DESK_ACTOR,
            kind="kill",
        )
        return _block(
            order,
            store=store,
            http_status=409,
            detail=KILL_BUY_DETAIL,
            gate="kill",
            audit_id=audit_id,
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
        )

    # 2. Mandate — fail closed if missing/corrupt; same inspect as /api/ops.
    if not snapshot.is_loaded:
        if snapshot.status == MANDATE_STATUS_MISSING:
            detail = (
                "Mandate missing — Desk orders fail closed until "
                "config/mandate.json is present."
            )
        elif snapshot.status == MANDATE_STATUS_CORRUPT:
            detail = (
                "Mandate corrupt — Desk orders fail closed: "
                f"{snapshot.error or 'unreadable mandate file'}."
            )
        else:
            detail = f"Mandate unusable (status={snapshot.status})."
        audit_id = audit.log_mandate_decision(
            order.symbol,
            side,
            False,
            detail,
            actor=DESK_ACTOR,
        )
        return _block(
            order,
            store=store,
            http_status=409,
            detail=detail,
            gate="mandate",
            audit_id=audit_id,
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
        )

    try:
        account = broker.get_account_summary()
    except Exception as exc:
        detail = f"Cannot evaluate risk: account snapshot failed: {exc}"
        audit_id = audit.log_risk_decision(
            order.symbol, side, float(order.quantity or 0), False, detail, actor=DESK_ACTOR
        )
        return _block(
            order,
            store=store,
            http_status=409,
            detail=detail,
            gate="risk",
            audit_id=audit_id,
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
        )

    ref_price = resolve_reference_price(order, account, quote_fn=quote_fn)
    eval_order = order.model_copy()
    if eval_order.limit_price is None and ref_price is not None:
        eval_order.limit_price = ref_price

    gate = _gate_for_snapshot(snapshot)
    mandate_decision = gate.evaluate(eval_order, ref_price)
    notional = None
    if ref_price is not None:
        try:
            notional = float(order.quantity) * float(ref_price)
        except (TypeError, ValueError):
            notional = None
    mandate_audit = audit.log_mandate_decision(
        order.symbol,
        side,
        mandate_decision.approved,
        mandate_decision.reason,
        notional=notional,
        actor=DESK_ACTOR,
    )
    if not mandate_decision.approved:
        detail = mandate_decision.reason or "Mandate rejected this order."
        return _block(
            order,
            store=store,
            http_status=409,
            detail=detail,
            gate="mandate",
            audit_id=mandate_audit,
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
        )

    # 3. RiskEngine — same kernel as the worker, not via SignalProcessor.
    risk_decision = engine.evaluate_order(eval_order, account)
    if not risk_decision.approved:
        detail = f"Risk Gate Rejected: {risk_decision.reason}"
        audit_id = audit.log_risk_decision(
            order.symbol,
            side,
            float(order.quantity or 0),
            False,
            detail,
            actor=DESK_ACTOR,
        )
        return _block(
            order,
            store=store,
            http_status=409,
            detail=detail,
            gate="risk",
            audit_id=audit_id,
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
        )

    risk_audit = audit.log_risk_decision(
        order.symbol,
        side,
        float(order.quantity or 0),
        True,
        "approved_via_desk_path",
        actor=DESK_ACTOR,
    )

    # 4. OrderManager + journal. Stamp RISK_APPROVED only after real evaluate.
    order.status = OrderStatus.RISK_APPROVED
    manager = OrderManager(broker=broker, store=store)
    submitted = manager.submit_new_order(order)
    submit_audit = audit.log_order_submission(
        submitted.symbol,
        side,
        float(submitted.quantity or 0),
        submitted.limit_price,
        submitted.id,
        submitted.status.value if hasattr(submitted.status, "value") else str(submitted.status),
        actor=DESK_ACTOR,
    )
    journaled, journal_err = _journal_order(
        submitted, audit_id=submit_audit or risk_audit
    )
    blocked_broker = str(getattr(submitted.status, "value", submitted.status)).lower() == "rejected"
    if not journaled:
        if blocked_broker:
            detail = _reject_not_persisted_detail(
                submitted.error_message or "Order rejected",
                journal_err or "journal write failed",
                "broker",
            )
            return DeskPlaceResult(
                order=submitted,
                blocked=True,
                http_status=500,
                detail=detail,
                gate="broker",
                mandate_status=snapshot.status,
                mandate_path=snapshot.path,
                audit_id=submit_audit or risk_audit,
                journaled=False,
            )
        return DeskPlaceResult(
            order=submitted,
            blocked=False,
            http_status=502,
            detail=_journal_failed_after_broker_detail(
                submitted, journal_err or "journal write failed"
            ),
            gate="journal",
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
            audit_id=submit_audit or risk_audit,
            journaled=False,
        )
    if blocked_broker:
        return DeskPlaceResult(
            order=submitted,
            blocked=True,
            http_status=400,
            detail=submitted.error_message or "Order rejected",
            gate="broker",
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
            audit_id=submit_audit or risk_audit,
            journaled=True,
        )
    # Count daily mandate only after broker accept + durable journal (not on 502 retry).
    if side == "buy":
        gate.record_order()
    return DeskPlaceResult(
        order=submitted,
        blocked=False,
        http_status=200,
        gate=None,
        mandate_status=snapshot.status,
        mandate_path=snapshot.path,
        audit_id=submit_audit or risk_audit,
        journaled=True,
    )
