"""H1 realtime Intent consumer (thin hook).

Dual-clock (§4.4): H1 consumes unexpired active intents and hands them to
the existing risk/signal path through M8. H1 must never block on LLM.

This is NOT a full harness_realtime / H2 worker split.

TODO(full-h1-h2-split): promote this module to a dedicated ~1s H1/H2 loop
separate from TradingWorker (which remains H3-cadence today). Do not pull
LLM, lab debate, or desk API modules into this file. Do not call a
broker from here — process_signal (M8 RiskEngine → M9) is the only
execution hand-off.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.trading.intents.models import Intent, IntentSide, IntentStatus
from backend.trading.intents.store import JSONIntentStore
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
        store.set_status(intent.intent_id, IntentStatus.EXPIRED)
        out.append(
            ConsumeResult(
                intent_id=intent.intent_id,
                action="expired",
                symbol=intent.symbol,
                reason="expiry",
            )
        )
    return out


def size_intent_qty(
    intent: Intent,
    *,
    price: float,
    equity: float,
    limits: Optional[RiskLimits] = None,
) -> int:
    """M8 sizer: conviction_tier picks a fraction of the RiskLimits envelope."""
    limits = limits or RiskLimits()
    if price <= 0 or equity <= 0:
        return 0
    scale = TIER_ENVELOPE_SCALE.get(intent.conviction_tier, 0.0)
    if scale <= 0:
        return 0
    qty = shares_to_buy(
        portfolio_value=float(equity) * scale,
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
) -> List[ConsumeResult]:
    """H1 consume: skip expired/non-active; size via M8; hand to process_signal.

    Never calls LLM. Never talks to a broker. process_signal must be the
    existing M8→M9 path (SignalProcessor.process_signal).
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
        if intent.veto_codes:
            store.set_status(intent.intent_id, IntentStatus.CANCELLED)
            results.append(
                ConsumeResult(
                    intent_id=intent.intent_id,
                    action="cancelled",
                    symbol=intent.symbol,
                    reason="veto_codes",
                )
            )
            continue
        if not intent.is_consumable(when):
            results.append(
                ConsumeResult(
                    intent_id=intent.intent_id,
                    action="skipped_inactive",
                    symbol=intent.symbol,
                )
            )
            continue
        price = get_price(intent.symbol)
        try:
            px = float(price) if price is not None else 0.0
        except (TypeError, ValueError):
            px = 0.0
        if px <= 0:
            results.append(
                ConsumeResult(
                    intent_id=intent.intent_id,
                    action="skipped_no_price",
                    symbol=intent.symbol,
                    reason="no_reference_price",
                )
            )
            continue
        qty = size_intent_qty(intent, price=px, equity=equity, limits=limits)
        if qty <= 0:
            results.append(
                ConsumeResult(
                    intent_id=intent.intent_id,
                    action="skipped_zero_qty",
                    symbol=intent.symbol,
                    reason="m8_sizer_zero",
                )
            )
            continue

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
        try:
            order = process_signal(
                signal,
                account_summary=account_summary,
                risk_extras=risk_extras or {},
            )
        except TypeError:
            order = process_signal(signal)
        except Exception as exc:
            logger.warning("H1 consume process_signal failed for %s: %s", intent.intent_id, exc)
            results.append(
                ConsumeResult(
                    intent_id=intent.intent_id,
                    action="skipped_process_error",
                    symbol=intent.symbol,
                    qty=qty,
                    reason=str(exc),
                )
            )
            continue

        store.set_status(intent.intent_id, IntentStatus.CONSUMED)
        order_id = None
        if order is not None:
            order_id = str(getattr(order, "id", None) or (order.get("id") if isinstance(order, dict) else "") or "") or None
        results.append(
            ConsumeResult(
                intent_id=intent.intent_id,
                action="consumed",
                symbol=intent.symbol,
                qty=qty,
                order_id=order_id,
                extra={"idempotency_key": signal["idempotency_key"]},
            )
        )
    return results
