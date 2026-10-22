"""H1 realtime Intent consumer (thin hook).

Dual-clock (§4.4): H1 consumes unexpired active intents and hands them to
the existing risk/signal path through M8. H1 must never block on LLM.

This is NOT a full harness_realtime / H2 worker split.

TODO(full-h1-h2-split): promote this module to a dedicated ~1s H1/H2 loop
separate from TradingWorker (which remains H3-cadence today). Do not pull
LLM, lab debate, or desk API modules into this file. Do not call a
broker from here — process_signal (M8 RiskEngine → M9) is the only
execution hand-off.

Intent.status consumed (§5.6) means H1 handed the intent to process_signal
so it will not retry. It is NOT a happy fill. Risk rejects stay consumed
and are distinguished via ConsumeResult.action=consumed_risk_rejected plus
the journal TradeLogEntry (status=risk_rejected, intent_id set).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.trading.engine.signal_processor import JournalWriteError
from backend.trading.intents.models import Intent, IntentSide, IntentStatus
from backend.trading.intents.store import JSONIntentStore
from backend.trading.models import Order, OrderSide, OrderType
from backend.trading.risk.gates import RiskLimits
from backend.trading.risk.position_sizer import cap_shares_to_notional, shares_to_buy

logger = logging.getLogger(__name__)

# Conviction selects a fraction of the M8 RiskLimits envelope. It cannot
# raise size above max_order_notional / max_position_concentration.
TIER_ENVELOPE_SCALE = {"low": 0.50, "med": 0.75, "high": 1.00}

# Kelly inputs used only as the existing StrategyBase defaults so H1 does
# not invent a new risk% constitution. Caps still come from RiskLimits.
_DEFAULT_WIN_RATE = 0.55
_DEFAULT_AVG_WIN_PCT = 0.08
_DEFAULT_AVG_LOSS_PCT = 0.04


GetPrice = Callable[[str], Optional[float]]
ProcessSignal = Callable[..., Any]


@dataclass
class ConsumeResult:
    intent_id: str
    action: str
    symbol: str = ""
    qty: Optional[int] = None
    order_id: Optional[str] = None
    reason: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def _utc(now: Optional[datetime] = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def expire_stale_intents(
    store: JSONIntentStore,
    *,
    now: Optional[datetime] = None,
) -> List[ConsumeResult]:
    """Mark active intents past expiry as expired. H1 never consumes these."""
    when = _utc(now)
    out: List[ConsumeResult] = []
    for intent in store.list(status=IntentStatus.ACTIVE.value, limit=1000):
        if not intent.is_expired(when):
            continue
        try:
            updated = store.set_status(intent.intent_id, IntentStatus.EXPIRED)
        except Exception as exc:
            logger.error(
                "H1 expire lost the race for intent_id=%s: %s",
                intent.intent_id,
                exc,
            )
            continue
        if updated is None or updated.status is not IntentStatus.EXPIRED:
            continue
        out.append(
            ConsumeResult(
                intent_id=intent.intent_id,
                action="expired",
                symbol=intent.symbol,
                reason="expiry",
            )
        )
    return out


def _long_position_qty(account: Any, symbol: str) -> float:
    """Shares currently long. Never negative. Desk does not allow short opens."""
    want = str(symbol or "").upper()
    if not want or account is None:
        return 0.0
    positions = getattr(account, "positions", None)
    if positions is None and isinstance(account, dict):
        positions = account.get("positions")
    total = 0.0
    for pos in positions or []:
        name = str(getattr(pos, "symbol", None) or (pos.get("symbol") if isinstance(pos, dict) else "") or "").upper()
        if name != want:
            continue
        raw = getattr(pos, "quantity", None)
        if raw is None and isinstance(pos, dict):
            raw = pos.get("quantity")
        try:
            qty = float(raw or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty > 0:
            total += qty
    return total


def size_intent_qty(
    intent: Intent,
    *,
    price: float,
    equity: float,
    limits: Optional[RiskLimits] = None,
    account_summary: Any = None,
    vix_multiplier: float = 1.0,
) -> int:
    """M8 sizer.

    Buys: conviction_tier picks a fraction of the RiskLimits envelope (Kelly).
    Sells: flatten-only from actual long qty. Never Kelly-buy math; never open a short.
    """
    if intent.side == IntentSide.SELL:
        held = _long_position_qty(account_summary, intent.symbol)
        return max(0, int(held))
    limits = limits or RiskLimits()
    if price <= 0 or equity <= 0:
        return 0
    scale = TIER_ENVELOPE_SCALE.get(intent.conviction_tier, 0.0)
    if scale <= 0:
        return 0
    try:
        vix_mult = float(vix_multiplier) if vix_multiplier else 1.0
    except (TypeError, ValueError):
        vix_mult = 1.0
    if vix_mult <= 0:
        vix_mult = 1.0
    qty = shares_to_buy(
        portfolio_value=float(equity) * scale * vix_mult,
        current_price=float(price),
        win_rate=_DEFAULT_WIN_RATE,
        avg_win_pct=_DEFAULT_AVG_WIN_PCT,
        avg_loss_pct=_DEFAULT_AVG_LOSS_PCT,
        kelly_fraction_scale=float(limits.kelly_scale),
        max_fraction=float(limits.max_position_concentration),
        min_shares=1,
    )
    return cap_shares_to_notional(
        qty,
        float(price),
        float(limits.max_order_notional) * scale,
    )


def stop_price_from_intent(intent: Intent, price: float) -> Optional[float]:
    """Convert intent-layer pct stop to a price for the sized order. Not a ticket."""
    if intent.stop is None:
        return None
    if intent.stop.type != "pct":
        return None
    if intent.side == IntentSide.BUY:
        return float(price) * (1.0 - float(intent.stop.value))
    return float(price) * (1.0 + float(intent.stop.value))


def _account_equity(account: Any) -> float:
    if account is None:
        return 0.0
    for name in ("portfolio_value", "equity", "cash"):
        raw = getattr(account, name, None)
        if raw is None and isinstance(account, dict):
            raw = account.get(name)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _order_field(order: Any, name: str, default: Any = None) -> Any:
    if order is None:
        return default
    if isinstance(order, dict):
        return order.get(name, default)
    return getattr(order, name, default)


def consume_action_for_order(order: Any) -> str:
    """Map a process_signal result to ConsumeResult.action.

    Intent.status stays consumed (§5.6). This action is observability only:
    consumed_risk_rejected means M8 rejected — not a happy fill.
    """
    status = _order_field(order, "status")
    status_val = str(status.value if hasattr(status, "value") else status or "").lower()
    err = str(_order_field(order, "error_message") or _order_field(order, "error") or "")
    if "risk gate rejected" in err.lower() or status_val in {"risk_rejected"}:
        return "consumed_risk_rejected"
    if "journal_write_failed" in err.lower():
        return "consumed_journal_failed"
    return "consumed"


def consume_active_intents(
    store: JSONIntentStore,
    *,
    process_signal: ProcessSignal,
    get_price: GetPrice,
    account_summary: Any = None,
    now: Optional[datetime] = None,
    killed: bool = False,
    limits: Optional[RiskLimits] = None,
    risk_extras: Optional[Dict[str, Any]] = None,
    mandate_gate: Any = None,
    vix_multiplier: float = 1.0,
) -> List[ConsumeResult]:
    """H1 consume: skip expired/non-active; full M8; hand to process_signal.

    Never calls LLM. Never talks to a broker. process_signal must be the
    existing M8→M9 path (SignalProcessor.process_signal). MandateGate is
    evaluated before size/submit (same as the strategy scan path). A buy
    that is handed to process_signal and is not risk-rejected counts via
    MandateGate.record_order so max_daily_orders applies inside this loop.
    Kill, cooldown, VIX, and price_history are supplied by the caller via
    killed / risk_extras / vix_multiplier.

    Per-intent exceptions before claim are fail-closed for that intent this
    tick (no order, status stays active) and do not abort the rest of the loop.
    """
    when = _utc(now)
    results = expire_stale_intents(store, now=when)
    if killed:
        for intent in store.list(status=IntentStatus.ACTIVE.value, limit=1000):
            results.append(
                ConsumeResult(
                    intent_id=intent.intent_id,
                    action="skipped_kill",
                    symbol=intent.symbol,
                    reason="kill_switch",
                )
            )
        return results

    limits = limits or RiskLimits()
    equity = _account_equity(account_summary)

    for intent in store.list(status=IntentStatus.ACTIVE.value, limit=1000):
        try:
            result = _consume_one_intent(
                store,
                intent,
                process_signal=process_signal,
                get_price=get_price,
                account_summary=account_summary,
                when=when,
                limits=limits,
                equity=equity,
                risk_extras=risk_extras,
                mandate_gate=mandate_gate,
                vix_multiplier=vix_multiplier,
            )
            results.append(result)
        except Exception as exc:
            logger.error(
                "H1 consume failed for intent_id=%s symbol=%s: %s",
                intent.intent_id,
                intent.symbol,
                exc,
                exc_info=True,
            )
            results.append(
                ConsumeResult(
                    intent_id=intent.intent_id,
                    action="skipped_process_error",
                    symbol=intent.symbol,
                    reason=str(exc),
                )
            )
    return results


def _mandate_draft(intent: Intent, *, qty: int, price: float) -> Order:
    side = OrderSide.BUY if intent.side == IntentSide.BUY else OrderSide.SELL
    return Order(
        id=f"mandate-intent-{intent.intent_id}",
        symbol=intent.symbol,
        side=side,
        order_type=OrderType.LIMIT,
        quantity=float(qty),
        limit_price=float(price),
        idempotency_key=f"mandate-intent-{intent.intent_id}",
    )


def _mandate_blocks(mandate_gate: Any, intent: Intent, *, qty: int, price: float) -> Optional[str]:
    if mandate_gate is None:
        return None
    evaluate = getattr(mandate_gate, "evaluate", None)
    if not callable(evaluate):
        return None
    decision = evaluate(_mandate_draft(intent, qty=qty, price=price), price)
    approved = bool(getattr(decision, "approved", True))
    if approved:
        return None
    return str(getattr(decision, "reason", None) or "mandate_blocked")


def _consume_one_intent(
    store: JSONIntentStore,
    intent: Intent,
    *,
    process_signal: ProcessSignal,
    get_price: GetPrice,
    account_summary: Any,
    when: datetime,
    limits: RiskLimits,
    equity: float,
    risk_extras: Optional[Dict[str, Any]],
    mandate_gate: Any,
    vix_multiplier: float,
) -> ConsumeResult:
    if intent.veto_codes:
        try:
            store.set_status(intent.intent_id, IntentStatus.CANCELLED)
        except Exception as exc:
            logger.error(
                "H1 veto-cancel lost the race for intent_id=%s: %s",
                intent.intent_id,
                exc,
            )
            return ConsumeResult(
                intent_id=intent.intent_id,
                action="skipped_claim_lost",
                symbol=intent.symbol,
                reason="veto_codes_race",
            )
        return ConsumeResult(
            intent_id=intent.intent_id,
            action="cancelled",
            symbol=intent.symbol,
            reason="veto_codes",
        )
    if not intent.is_consumable(when):
        return ConsumeResult(
            intent_id=intent.intent_id,
            action="skipped_inactive",
            symbol=intent.symbol,
        )
    price = get_price(intent.symbol)
    try:
        px = float(price) if price is not None else 0.0
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0:
        return ConsumeResult(
            intent_id=intent.intent_id,
            action="skipped_no_price",
            symbol=intent.symbol,
            reason="no_reference_price",
        )

    # Same MandateGate as the strategy scan path — before Kelly/flatten size.
    blocked = _mandate_blocks(mandate_gate, intent, qty=1, price=px)
    if blocked:
        return ConsumeResult(
            intent_id=intent.intent_id,
            action="skipped_mandate",
            symbol=intent.symbol,
            reason=blocked,
        )

    qty = size_intent_qty(
        intent,
        price=px,
        equity=equity,
        limits=limits,
        account_summary=account_summary,
        vix_multiplier=vix_multiplier,
    )
    if qty <= 0:
        sell = intent.side == IntentSide.SELL
        return ConsumeResult(
            intent_id=intent.intent_id,
            action="skipped_no_long" if sell else "skipped_zero_qty",
            symbol=intent.symbol,
            qty=0,
            reason="no_long_position" if sell else "m8_sizer_zero",
        )

    blocked = _mandate_blocks(mandate_gate, intent, qty=qty, price=px)
    if blocked:
        return ConsumeResult(
            intent_id=intent.intent_id,
            action="skipped_mandate",
            symbol=intent.symbol,
            reason=blocked,
        )

    stop_px = stop_price_from_intent(intent, px)
    signal = {
        "id": f"intent-{intent.intent_id}",
        "symbol": intent.symbol,
        "side": intent.side.value,
        "quantity": qty,
        "limit_price": px,
        "order_type": "limit",
        "stop_loss_price": stop_px,
        "order_class": "simple",
        "idempotency_key": f"intent:{intent.intent_id}",
        "strategy_id": intent.strategy_id,
        "intent_id": intent.intent_id,
        "conviction_tier": intent.conviction_tier,
    }

    # CAS active → consumed before process_signal so a later cancel is 409
    # and cannot race a submit. Cancel-first makes claim return None (no order).
    claimed = store.claim_for_consume(intent.intent_id)
    if claimed is None:
        return ConsumeResult(
            intent_id=intent.intent_id,
            action="skipped_claim_lost",
            symbol=intent.symbol,
            reason="cancel_or_expiry_won",
        )

    try:
        try:
            order = process_signal(
                signal,
                account_summary=account_summary,
                risk_extras=risk_extras or {},
            )
        except TypeError:
            order = process_signal(signal)
    except JournalWriteError as exc:
        logger.error(
            "H1 journal write failed after submit intent_id=%s symbol=%s: %s",
            intent.intent_id,
            intent.symbol,
            exc,
            exc_info=True,
        )
        order = getattr(exc, "order", None)
        order_id = None
        if order is not None:
            order_id = str(
                _order_field(order, "id") or _order_field(order, "order_id") or ""
            ) or None
        return ConsumeResult(
            intent_id=intent.intent_id,
            action="consumed_journal_failed",
            symbol=intent.symbol,
            qty=qty,
            order_id=order_id,
            reason="journal_write_failed",
            extra={"idempotency_key": signal["idempotency_key"]},
        )
    except Exception as exc:
        logger.error(
            "H1 process_signal failed after claim intent_id=%s symbol=%s: %s",
            intent.intent_id,
            intent.symbol,
            exc,
            exc_info=True,
        )
        return ConsumeResult(
            intent_id=intent.intent_id,
            action="consumed_process_error",
            symbol=intent.symbol,
            qty=qty,
            reason=str(exc),
            extra={"idempotency_key": signal["idempotency_key"]},
        )

    order_id = None
    if order is not None:
        order_id = str(
            _order_field(order, "id") or _order_field(order, "order_id") or ""
        ) or None
    action = consume_action_for_order(order)
    # Strategy scan counts a buy only after process_signal returns without a
    # risk reject. Without this, every active buy in the tick sees the same
    # orders_today and max_daily_orders does not hold.
    if action == "consumed" and intent.side == IntentSide.BUY:
        record_order = getattr(mandate_gate, "record_order", None)
        if callable(record_order):
            record_order()
    return ConsumeResult(
        intent_id=intent.intent_id,
        action=action,
        symbol=intent.symbol,
        qty=qty,
        order_id=order_id,
        reason="risk_rejected" if action == "consumed_risk_rejected" else None,
        extra={"idempotency_key": signal["idempotency_key"]},
    )
