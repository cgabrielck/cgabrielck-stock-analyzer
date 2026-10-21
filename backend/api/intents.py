"""Desk IntentStore API — local/unverified L1 intents. Not live. Not orders.

H3 writers and Desk list/get/write. Consume (H1) is harness_realtime, not an
HTTP broker bypass.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.api.desk_auth import require_operator
from backend.trading.intents.models import (
    INTENT_SCHEMA_VERSION,
    Intent,
    IntentStatus,
    IntentValidationError,
)
from backend.trading.intents.store import (
    IntentConflictError,
    JSONIntentStore,
    create_intent_store,
)
from backend.trading.intents.writer import upsert_active_intent

router = APIRouter(tags=["intents"])

_store: Optional[JSONIntentStore] = None


def get_intent_store() -> JSONIntentStore:
    global _store
    if _store is None:
        _store = create_intent_store()
    return _store


def _envelope(**extra: Any) -> Dict[str, Any]:
    payload = {
        "schema_version": INTENT_SCHEMA_VERSION,
        "verified": False,
        "live": False,
        "local": True,
        "unverified": True,
        "disclaimer": (
            "IntentStore is a local L1 ledger (intent.v0). "
            "Not an order, not a fill, not live, not verified."
        ),
    }
    payload.update(extra)
    return payload


def _dump(intent: Intent) -> Dict[str, Any]:
    return intent.model_dump_json_dict()


class IntentIn(BaseModel):
    """Write body. Extra L2 fields are rejected by parse_intent / extra=forbid."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = INTENT_SCHEMA_VERSION
    intent_id: Optional[str] = None
    symbol: str
    side: str
    stop: Optional[Dict[str, Any]] = None
    conviction_tier: str
    expiry: datetime
    as_of: Optional[datetime] = None
    strategy_id: Optional[str] = None
    card_id: Optional[str] = None
    veto_codes: List[str] = Field(default_factory=list)


@router.get("/api/intents")
def list_intents(
    status: str = "all",
    symbol: Optional[str] = None,
    limit: int = 100,
    _auth: None = Depends(require_operator),
) -> Dict[str, Any]:
    store = get_intent_store()
    want = None if str(status or "all").lower() in {"all", ""} else status
    rows = store.list(status=want, symbol=symbol, limit=max(1, min(int(limit or 100), 400)))
    return _envelope(intents=[_dump(row) for row in rows], count=len(rows))


@router.get("/api/intents/{intent_id}")
def get_intent(intent_id: str, _auth: None = Depends(require_operator)) -> Dict[str, Any]:
    store = get_intent_store()
    found = store.get(intent_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Intent not found")
    return _envelope(intent=_dump(found))


@router.post("/api/intents")
def write_intent(body: IntentIn, _auth: None = Depends(require_operator)) -> Dict[str, Any]:
    if body.schema_version != INTENT_SCHEMA_VERSION:
        raise HTTPException(
            status_code=422,
            detail=f"schema_version must be {INTENT_SCHEMA_VERSION}",
        )
    store = get_intent_store()
    try:
        intent = upsert_active_intent(
            store,
            symbol=body.symbol,
            side=body.side,
            conviction_tier=body.conviction_tier,
            expiry=body.expiry,
            as_of=body.as_of,
            stop=body.stop,
            strategy_id=body.strategy_id,
            card_id=body.card_id,
            veto_codes=list(body.veto_codes or []),
            intent_id=body.intent_id,
        )
    except IntentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        # Surface pydantic extra-forbid (qty, limit_price, …) as 422, not 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _envelope(intent=_dump(intent))


@router.post("/api/intents/{intent_id}/cancel")
def cancel_intent(intent_id: str, _auth: None = Depends(require_operator)) -> Dict[str, Any]:
    store = get_intent_store()
    try:
        updated = store.set_status(intent_id, IntentStatus.CANCELLED)
    except IntentConflictError as exc:
        current = exc.current_status.value if exc.current_status is not None else "unknown"
        raise HTTPException(
            status_code=409,
            detail=f"Intent is {current}; only active intents can be cancelled",
        ) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Intent not found")
    return _envelope(intent=_dump(updated))
