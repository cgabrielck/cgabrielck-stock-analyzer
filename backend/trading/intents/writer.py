"""H3 decision-cycle write path — strategies/advisory upsert active intents.

H3 may run LLM / heavy work. Results are written here as Intent v0 only.
This module never calls a broker or order manager, and never imports lab debate.

Final qty is NOT computed here. H1 consume + M8 sizer own sizing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from backend.trading.intents.models import (
    Intent,
    IntentStatus,
    IntentValidationError,
    parse_intent,
)
from backend.trading.intents.store import JSONIntentStore

logger = logging.getLogger(__name__)


def upsert_active_intent(
    store: JSONIntentStore,
    *,
    symbol: str,
    side: str,
    conviction_tier: str,
    expiry: Union[datetime, str],
    as_of: Optional[Union[datetime, str]] = None,
    stop: Optional[Dict[str, Any]] = None,
    strategy_id: Optional[str] = None,
    card_id: Optional[str] = None,
    veto_codes: Optional[List[str]] = None,
    intent_id: Optional[str] = None,
) -> Intent:
    """Upsert one active intent. Replaces prior active intent for symbol+side.

    Raises IntentValidationError if the payload is not intent.v0 (including
    forbidden L2 fields, missing buy stop, or non-empty veto_codes).
    """
    codes = [str(item).strip() for item in (veto_codes or []) if str(item).strip()]
    if codes:
        raise IntentValidationError(
            "H3 will not upsert an active intent with veto_codes; "
            "vetoed setups stay out of IntentStore."
        )
    payload: Dict[str, Any] = {
        "schema_version": "intent.v0",
        "symbol": symbol,
        "side": side,
        "conviction_tier": conviction_tier,
        "expiry": expiry,
        "as_of": as_of or datetime.now(timezone.utc),
        "stop": stop,
        "strategy_id": strategy_id,
        "card_id": card_id,
        "veto_codes": [],
        "status": IntentStatus.ACTIVE.value,
    }
    if intent_id:
        payload["intent_id"] = intent_id
    intent = parse_intent(payload)

    cancelled = 0
    for existing in store.list(status=IntentStatus.ACTIVE.value, symbol=intent.symbol, limit=500):
        if existing.intent_id == intent.intent_id:
            continue
        if existing.side != intent.side:
            continue
        store.set_status(existing.intent_id, IntentStatus.CANCELLED)
        cancelled += 1
    stored = store.save(intent)
    logger.info(
        "H3 upsert intent %s %s %s tier=%s (cancelled_prior=%s)",
        stored.intent_id,
        stored.side.value,
        stored.symbol,
        stored.conviction_tier,
        cancelled,
    )
    return stored
