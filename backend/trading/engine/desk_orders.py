"""Desk / lab manual place-order path (R1-SAFE).

Manual Desk stays a separate path from IntentStore / H1 / SignalProcessor
(R1-PATH, DESIGN — not wired here). This module still enforces, in order:

    kill (buy and sell — D-sell-kill) → Mandate (fail-closed) → RiskEngine
    → OrderManager + journal/audit  (or shadow simulate + journal)

so accepted and rejected orders are traceable. FastAPI ``POST /api/orders``
and the Streamlit lab dashboard both call ``place_desk_order`` — there is
no third evaluate-skip path. ``/api/ops`` and this path share
``inspect_mandate()``.

S3: ``account is None`` (or a failed/empty snapshot) is a reject. Never
stamp ``RISK_APPROVED`` / ``FILLED`` / verified / live without evaluate.

Kill on this path blocks sell as well as buy (409 + halt reason). Worker
flatten-through-kill is a leftover implementation note, not a binding
Desk product rule (ADR-003 / MODULAR / AUDIT do not require it).

R4: max_daily_orders is a durable UTC-day counter under DATA_DIR, shared
by Desk and worker MandateGate instances. Restart does not reset the day.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from backend.trading.broker import BrokerAdapter
from backend.trading.engine.order_manager import OrderManager, OrderStore
from backend.trading.engine.stamp import VENUE_STATUSES, stamp_risk_approved
from backend.trading.engine.sticky_idempotency import (
    DO_NOT_BLIND_RETRY,
    clear_sticky,
    detach_if_would_clobber_sticky,
    find_sticky_order,
    pin_sticky,
    unknown_accept_detail,
)
from backend.trading.models import AccountSummary, Order, OrderSide, OrderStatus, OrderType
from backend.trading.risk.gates import RiskEngine, positive_price
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
KILL_SELL_DETAIL = "Kill switch is on — sells blocked."
NO_CONSTRAINING_PRICE_DETAIL = (
    "Cannot approve order without a positive price that constrains "
    "the submitted order."
)
ACCOUNT_MISSING_DETAIL = "Cannot evaluate risk: account snapshot missing."
SHADOW_NO_POSITIVE_FILL_DETAIL = (
    "Cannot shadow-fill without a positive fill price. "
    "Market/stop orders have no constraining limit; a $0 fill is not a fill."
)
_ACCOUNT_UNSET = object()
_LAB_EXECUTION_MODES = frozenset({"paper", "shadow"})

# Alpaca MarketOrderRequest / StopOrderRequest ignore limit_price.
_UNCONSTRAINED_ORDER_TYPES = frozenset({OrderType.MARKET.value, OrderType.STOP.value})
_LIMIT_BOUND_ORDER_TYPES = frozenset({OrderType.LIMIT.value, OrderType.STOP_LIMIT.value})

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


def _order_type_key(order: Order) -> str:
    otype = getattr(order, "order_type", None)
    raw = otype.value if isinstance(otype, OrderType) else otype
    return str(raw or "").lower()


def constraining_order_price(order: Order) -> Optional[float]:
    """Price that actually binds the broker request, else None.

    ``limit_price`` is ignored for market/stop: Alpaca ``MarketOrderRequest``
    / ``StopOrderRequest`` do not send it, so a client-supplied tiny/zero/
    negative limit must not understate Mandate/RiskEngine notional.
    """
    key = _order_type_key(order)
    if key in _UNCONSTRAINED_ORDER_TYPES:
        return None
    if key in _LIMIT_BOUND_ORDER_TYPES:
        return positive_price(order.limit_price)
    return None


def _mark_price(account: Optional[AccountSummary], symbol: str) -> Optional[float]:
    if account is None:
        return None
    want = str(symbol or "").upper()
    for pos in account.positions or []:
        if str(getattr(pos, "symbol", "") or "").upper() != want:
            continue
        px = positive_price(getattr(pos, "current_price", None))
        if px is not None:
            return px
    return None


def resolve_reference_price(
    order: Order,
    account: Optional[AccountSummary] = None,
    quote_fn: Optional[Callable[[str], Optional[float]]] = None,
) -> Optional[float]:
    """Positive price for notional: constraining limit, else mark, else quote.

    Never prefers an unconstrained ``limit_price`` (market/stop, or limit<=0).

    Sell limit/stop-limit: the limit is a floor (fill at limit or better). A
    tiny client limit must not understate vs mark/quote — use
    ``max(limit, mark/quote)``. Without a mark/quote, return None (fail closed)
    rather than treating the tiny limit as honest sell exposure.
    """
    bound = constraining_order_price(order)
    sell = _side_value(order) == "sell"
    if bound is not None and not sell:
        return bound
    symbol = str(order.symbol or "").upper()
    mark = _mark_price(account, symbol)
    if mark is not None:
        market_px = mark
    else:
        fn = quote_fn or default_quote_price
        try:
            market_px = positive_price(fn(symbol))
        except Exception:
            logger.debug("desk quote_fn failed for %s", symbol, exc_info=True)
            market_px = None
    if bound is not None:
        return max(bound, market_px) if market_px is not None else None
    return market_px


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
    """MandateGate backed by the durable Desk+worker daily counter (R4).

    The gate object may be reused in-process; the count itself lives on
    disk so a worker restart or a sibling Desk process sees the same
    UTC-day total.
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
    """Test helper — drop the process-local gate cache (count stays on disk)."""
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
        detach_if_would_clobber_sticky(store, order)
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
            f"{persist_err}. Desk does not claim a durable audit trail. "
            "Do not blind-retry with a new idempotency key. "
            f"Reuse idempotency_key={getattr(order, 'idempotency_key', '')}."
        ),
        "journaled": False,
        "broker_submitted": True,
        "order_id": order_id,
        "symbol": getattr(order, "symbol", None),
        "idempotency_key": getattr(order, "idempotency_key", None),
        "retry": DO_NOT_BLIND_RETRY,
        "acceptance": "unknown",
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


def _sticky_replay_detail_suffix_ok(detail: Dict[str, Any], order: Order) -> Dict[str, Any]:
    detail["retry"] = DO_NOT_BLIND_RETRY
    detail["idempotency_key"] = order.idempotency_key
    detail["acceptance"] = "unknown"
    return detail


def _replay_sticky_venue(
    sticky: Order,
    *,
    store: OrderStore,
    snapshot: MandateSnapshot,
) -> DeskPlaceResult:
    """Journal an already-accepted sticky order. Do not call the broker."""
    journaled, journal_err = _journal_order(sticky, audit_id=None)
    if journaled:
        clear_sticky(store, sticky)
        return DeskPlaceResult(
            order=sticky,
            blocked=False,
            http_status=200,
            gate=None,
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
            journaled=True,
        )
    pin_sticky(store, sticky)
    detail = _sticky_replay_detail_suffix_ok(
        _journal_failed_after_broker_detail(sticky, journal_err or "journal write failed"),
        sticky,
    )
    return DeskPlaceResult(
        order=sticky,
        blocked=False,
        http_status=502,
        detail=detail,
        gate="journal",
        mandate_status=snapshot.status,
        mandate_path=snapshot.path,
        journaled=False,
    )


def _sticky_blind_result(
    sticky: Order,
    *,
    store: OrderStore,
    snapshot: MandateSnapshot,
) -> DeskPlaceResult:
    """A new idempotency key must not become a second broker submit."""
    if sticky.status == OrderStatus.ACCEPTANCE_UNKNOWN:
        return DeskPlaceResult(
            order=sticky,
            blocked=False,
            http_status=502,
            detail=unknown_accept_detail(sticky),
            gate="broker",
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
            journaled=False,
        )
    return _replay_sticky_venue(sticky, store=store, snapshot=snapshot)


def place_desk_order(
    order: Order,
    *,
    broker: BrokerAdapter,
    store: OrderStore,
    risk_engine: Optional[RiskEngine] = None,
    quote_fn: Optional[Callable[[str], Optional[float]]] = None,
    mandate_path: Any = None,
    account: Any = _ACCOUNT_UNSET,
    execution_mode: str = "paper",
) -> DeskPlaceResult:
    """Evaluate a manual Desk/lab order, then paper-submit or shadow-simulate.

    Does not call SignalProcessor / IntentStore. User-supplied quantity is
    kept (no Kelly resize) but still has to pass Mandate + RiskEngine.

    ``account is None`` (explicit) or a failed/empty snapshot → reject (S3).
    FastAPI omits ``account`` so this path fetches from the broker.
    Streamlit passes the page snapshot, including ``None``.
    """
    engine = risk_engine or _default_risk_engine
    side = _side_value(order)
    snapshot = inspect_mandate(mandate_path)
    mode = str(execution_mode or "paper").lower()

    # 502 sticky: a fresh key must not submit again while acceptance is
    # unknown or the broker order is not durably journaled. Checked before
    # kill/mandate so the response stays "do not blind-retry", not a new reject.
    safe_unknown_retry = False
    if mode != "shadow":
        sticky_hold = find_sticky_order(store, symbol=order.symbol, side=side)
        if sticky_hold is not None and sticky_hold.idempotency_key != order.idempotency_key:
            return _sticky_blind_result(sticky_hold, store=store, snapshot=snapshot)
        if sticky_hold is not None and sticky_hold.idempotency_key == order.idempotency_key:
            if sticky_hold.status in VENUE_STATUSES:
                return _replay_sticky_venue(sticky_hold, store=store, snapshot=snapshot)
            if sticky_hold.status == OrderStatus.ACCEPTANCE_UNKNOWN:
                safe_unknown_retry = True

    # 1. Kill — Desk buy (R6) and sell (D-sell-kill). Same honesty: 409,
    # no broker submit, clear halt reason. Not flatten-through-kill.
    if kill_switch.is_halted():
        detail = KILL_SELL_DETAIL if side == "sell" else KILL_BUY_DETAIL
        audit_id = audit.log_event(
            "kill_switch_check",
            {
                "halted": True,
                "reason": kill_switch.get_reason() or detail,
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
            detail=detail,
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

    if mode not in _LAB_EXECUTION_MODES:
        detail = f"Unknown execution_mode: {execution_mode}."
        return _block(
            order,
            store=store,
            http_status=400,
            detail=detail,
            gate="risk",
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
        )

    if account is _ACCOUNT_UNSET:
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
    if account is None:
        detail = ACCOUNT_MISSING_DETAIL
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
    bound = constraining_order_price(order)
    otype_key = _order_type_key(order)
    # Limit/stop-limit on the wire must carry a positive limit; a quote does
    # not constrain LimitOrderRequest. Market/stop need mark/quote instead.
    # Sell limit/stop-limit: limit is a floor — tiny limit understates vs mark.
    if otype_key in _LIMIT_BOUND_ORDER_TYPES:
        if bound is None:
            approval_price = None
        elif side == "sell":
            approval_price = positive_price(ref_price)
        else:
            approval_price = bound
    else:
        approval_price = positive_price(ref_price)
    if approval_price is None:
        detail = NO_CONSTRAINING_PRICE_DETAIL
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

    eval_order = order.model_copy()
    # RiskEngine reads order.limit_price; stamp the honest approval price so a
    # dummy market/stop limit or a tiny sell-limit floor cannot understate
    # notional at the kernel.
    eval_order.limit_price = approval_price
    ref_price = approval_price

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

    # Shadow market/stop: Mandate/Risk used mark/quote on a copy. The wire
    # order still has no constraining limit. No-bar ShadowTradingEngine then
    # fills at ``limit_price or 0.0`` — a fake FILLED. Reject instead of
    # inventing $0. Limit no-bar instant-fill (O1–O6) is unchanged.
    if mode == "shadow" and positive_price(order.limit_price) is None:
        audit_id = audit.log_risk_decision(
            order.symbol,
            side,
            float(order.quantity or 0),
            False,
            SHADOW_NO_POSITIVE_FILL_DETAIL,
            actor=DESK_ACTOR,
        )
        return _block(
            order,
            store=store,
            http_status=409,
            detail=SHADOW_NO_POSITIVE_FILL_DETAIL,
            gate="shadow",
            audit_id=audit_id,
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
        )

    # R4: claim the daily slot under flock *before* broker so concurrent
    # Desk+worker evaluate-then-record cannot both submit at cap=1.
    reserved = False
    if (
        side == "buy"
        and mode != "shadow"
        and gate.mandate.max_daily_orders is not None
        and not safe_unknown_retry
    ):
        slot = gate.try_reserve()
        if not slot.approved:
            detail = slot.reason or "Daily order limit reached."
            audit_id = audit.log_mandate_decision(
                order.symbol, side, False, detail, notional=notional, actor=DESK_ACTOR
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
        reserved = True

    # 4. Stamp only after real Mandate + Risk evaluate, then paper or shadow.
    stamp_risk_approved(
        order,
        risk_decision=risk_decision,
        mandate_decision=mandate_decision,
        require_mandate=True,
    )
    manager = OrderManager(broker=broker, store=store)
    if mode == "shadow":
        from backend.trading.engine.shadow import ShadowTradingEngine

        submitted = ShadowTradingEngine(manager).simulate_submission(order)
        fill_px = positive_price(getattr(submitted, "filled_avg_price", None))
        status_key = str(getattr(submitted.status, "value", submitted.status)).lower()
        if status_key == "filled" and fill_px is None:
            audit_id = audit.log_risk_decision(
                order.symbol,
                side,
                float(order.quantity or 0),
                False,
                SHADOW_NO_POSITIVE_FILL_DETAIL,
                actor=DESK_ACTOR,
            )
            return _block(
                submitted,
                store=store,
                http_status=409,
                detail=SHADOW_NO_POSITIVE_FILL_DETAIL,
                gate="shadow",
                audit_id=audit_id,
                mandate_status=snapshot.status,
                mandate_path=snapshot.path,
            )
    else:
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
    if submitted.status == OrderStatus.ACCEPTANCE_UNKNOWN:
        # Not a fill and not a confirmed reject. Keep the key. Do not 200.
        pin_sticky(store, submitted)
        detail = unknown_accept_detail(submitted, journaled=journaled)
        return DeskPlaceResult(
            order=submitted,
            blocked=False,
            http_status=502,
            detail=detail,
            gate="broker",
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
            audit_id=submit_audit or risk_audit,
            journaled=journaled,
        )
    blocked_accept = str(getattr(submitted.status, "value", submitted.status)).lower() == "rejected"
    if not journaled:
        if blocked_accept:
            if reserved:
                gate.release_reservation()
            detail = _reject_not_persisted_detail(
                submitted.error_message or "Order rejected",
                journal_err or "journal write failed",
                "broker" if mode != "shadow" else "shadow",
            )
            return DeskPlaceResult(
                order=submitted,
                blocked=True,
                http_status=500,
                detail=detail,
                gate="broker" if mode != "shadow" else "shadow",
                mandate_status=snapshot.status,
                mandate_path=snapshot.path,
                audit_id=submit_audit or risk_audit,
                journaled=False,
            )
        fail_detail = _journal_failed_after_broker_detail(
            submitted, journal_err or "journal write failed"
        )
        if mode == "shadow":
            fail_detail["broker_submitted"] = False
            fail_detail.pop("retry", None)
            fail_detail.pop("acceptance", None)
            fail_detail.pop("idempotency_key", None)
            shadow_id = getattr(submitted, "id", None)
            id_bit = f" (id={shadow_id})" if shadow_id else ""
            fail_detail["message"] = (
                f"Shadow order was not journaled{id_bit}. "
                f"{journal_err or 'journal write failed'}. "
                "Lab does not claim a durable audit trail."
            )
        else:
            pin_sticky(store, submitted)
        return DeskPlaceResult(
            order=submitted,
            blocked=False,
            http_status=502,
            detail=fail_detail,
            gate="journal",
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
            audit_id=submit_audit or risk_audit,
            journaled=False,
        )
    if blocked_accept:
        if reserved or safe_unknown_retry:
            gate.release_reservation()
        return DeskPlaceResult(
            order=submitted,
            blocked=True,
            http_status=400,
            detail=submitted.error_message or "Order rejected",
            gate="broker" if mode != "shadow" else "shadow",
            mandate_status=snapshot.status,
            mandate_path=snapshot.path,
            audit_id=submit_audit or risk_audit,
            journaled=True,
        )
    # Cap path already reserved before broker. No-cap still persists the count.
    if side == "buy" and mode != "shadow" and not reserved and not safe_unknown_retry:
        gate.record_order()
    if journaled and submitted.status in VENUE_STATUSES:
        clear_sticky(store, submitted)
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


def place_lab_manual_order(
    *,
    broker: BrokerAdapter,
    store: OrderStore,
    symbol: str,
    side: str,
    quantity: float,
    order_type: str,
    limit_price: Optional[float],
    account: Optional[AccountSummary],
    execution_mode: str = "paper",
    risk_engine: Optional[RiskEngine] = None,
    quote_fn: Optional[Callable[[str], Optional[float]]] = None,
    mandate_path: Any = None,
) -> DeskPlaceResult:
    """Streamlit / lab manual surface — same gates as Desk, no parallel path.

    ``account is None`` is S3: reject, never stamp RISK_APPROVED / FILLED.
    """
    side_key = str(side or "").lower()
    type_key = str(order_type or "limit").lower()
    if type_key == "limit":
        otype = OrderType.LIMIT
    elif type_key == "market":
        otype = OrderType.MARKET
    else:
        otype = OrderType(type_key)
    order = Order(
        id=str(uuid.uuid4()),
        symbol=str(symbol or "").upper().strip(),
        side=OrderSide.BUY if side_key == "buy" else OrderSide.SELL,
        order_type=otype,
        quantity=float(quantity),
        limit_price=limit_price if type_key in ("limit", "stop_limit") else None,
        idempotency_key=str(uuid.uuid4()),
    )
    return place_desk_order(
        order,
        broker=broker,
        store=store,
        risk_engine=risk_engine,
        quote_fn=quote_fn,
        mandate_path=mandate_path,
        account=account,
        execution_mode=execution_mode,
    )
