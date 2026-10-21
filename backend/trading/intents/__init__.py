"""L1 IntentStore (intent.v0) — H3 writes, H1 consumes. Not an order ledger.

See docs/MODULAR_ARCHITECTURE.md §5.6 and §4.4.
"""
from __future__ import annotations

from backend.trading.intents.models import (
    CONVICTION_TIERS,
    FORBIDDEN_L2_FIELDS,
    INTENT_SCHEMA_VERSION,
    Intent,
    IntentSide,
    IntentStatus,
    IntentValidationError,
    StopSpec,
    parse_intent,
)
from backend.trading.intents.store import JSONIntentStore, create_intent_store, default_intent_path
from backend.trading.intents.writer import upsert_active_intent

__all__ = [
    "CONVICTION_TIERS",
    "FORBIDDEN_L2_FIELDS",
    "INTENT_SCHEMA_VERSION",
    "Intent",
    "IntentSide",
    "IntentStatus",
    "IntentValidationError",
    "JSONIntentStore",
    "StopSpec",
    "create_intent_store",
    "default_intent_path",
    "parse_intent",
    "upsert_active_intent",
]
