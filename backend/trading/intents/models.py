"""Intent v0 model — L1 runtime intent (H3 write / H1 consume).

Binds under ADR-003 Addendum A and MODULAR_ARCHITECTURE §5.6.
Intents are NOT orders. Final qty is produced only by the M8 sizer.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

INTENT_SCHEMA_VERSION = "intent.v0"
CONVICTION_TIERS = ("low", "med", "high")
INTENT_STATUSES = ("active", "expired", "consumed", "cancelled")
INTENT_SIDES = ("buy", "sell")
STOP_TYPES = ("pct",)

# §5.6 forbidden L2 / broker / constitution fields. Rejected even as extras.
FORBIDDEN_L2_FIELDS = frozenset(
    {
        "quantity",
        "qty",
        "shares",
        "notional",
        "size",
        "raw_size",
        "limit_price",
        "order_type",
        "order_class",
        "broker_order_id",
        "broker_id",
        "idempotency_key",
        "filled_avg_price",
        "filled_at",
        "filled_qty",
        "filled_quantity",
        "slippage_pct",
        "commission",
        "take_profit_price",
        "mandate",
        "kill",
        "kill_switch",
        "live",
        "allow_live",
        "is_live",
        "draft",
        "risk_approved",
        "submitted",
        "accepted",
        "partially_filled",
        "filled",
        "rejected",
        "reconciliation_error",
    }
)

# OrderStatus tokens that must never appear as Intent.status.
FORBIDDEN_STATUS_VALUES = frozenset(
    {
        "draft",
        "risk_approved",
        "submitted",
        "accepted",
        "partially_filled",
        "filled",
        "rejected",
        "reconciliation_error",
        "canceled",  # US spelling — Intent v0 uses cancelled
    }
)


class IntentValidationError(ValueError):
    """Raised when a payload is not a valid intent.v0 document."""


class IntentSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class IntentStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"


class StopSpec(BaseModel):
    """Intent-layer protective stop — not a broker bracket ticket."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., description="Stop style. intent.v0 allows pct only.")
    value: float = Field(..., description="Fraction for type=pct, e.g. 0.10 = 10%.")

    @field_validator("type")
    @classmethod
    def _type_pct(cls, value: str) -> str:
        token = str(value or "").strip().lower()
        if token not in STOP_TYPES:
            raise ValueError(f"stop.type must be one of {STOP_TYPES}, got {value!r}")
        return token

    @field_validator("value")
    @classmethod
    def _value_fraction(cls, value: float) -> float:
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("stop.value must be a number") from exc
        if not (0.0 < num <= 1.0):
            raise ValueError("stop.value must be a fraction in (0, 1]")
        return num


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError("datetime is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 datetime: {value!r}") from exc
    return _as_utc(parsed)


def _forbidden_keys(data: Dict[str, Any]) -> List[str]:
    hits: List[str] = []
    for key in data.keys():
        low = str(key).strip().lower()
        if low in FORBIDDEN_L2_FIELDS or low.startswith("filled_"):
            hits.append(str(key))
    return sorted(hits)


class Intent(BaseModel):
    """L1 runtime intent (schema_version=intent.v0)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=INTENT_SCHEMA_VERSION)
    intent_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    side: IntentSide
    stop: Optional[StopSpec] = None
    conviction_tier: str
    expiry: datetime
    as_of: datetime
    strategy_id: Optional[str] = None
    card_id: Optional[str] = None
    veto_codes: List[str] = Field(default_factory=list)
    status: IntentStatus = IntentStatus.ACTIVE

    @model_validator(mode="before")
    @classmethod
    def _reject_l2_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        hits = _forbidden_keys(data)
        if hits:
            raise ValueError(
                "Intent v0 forbids L2/broker fields: "
                + ", ".join(hits)
                + ". Final qty comes from the M8 sizer only."
            )
        return data

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: str) -> str:
        token = str(value or "").strip()
        if token != INTENT_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {INTENT_SCHEMA_VERSION!r}")
        return token

    @field_validator("intent_id")
    @classmethod
    def _intent_id(cls, value: str) -> str:
        token = str(value or "").strip()
        if not token:
            raise ValueError("intent_id is required")
        return token

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        token = str(value or "").strip().upper()
        if not token:
            raise ValueError("symbol is required")
        return token

    @field_validator("side", mode="before")
    @classmethod
    def _side(cls, value: Any) -> Any:
        if value is None:
            return value
        token = str(value).strip().lower()
        return token or value

    @field_validator("conviction_tier")
    @classmethod
    def _tier(cls, value: str) -> str:
        token = str(value or "").strip().lower()
        if token not in CONVICTION_TIERS:
            raise ValueError(f"conviction_tier must be one of {CONVICTION_TIERS}")
        return token

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: Any) -> Any:
        token = str(value or "").strip().lower()
        if token in FORBIDDEN_STATUS_VALUES:
            raise ValueError(
                f"status {token!r} is an OrderStatus/L2 value; "
                f"Intent v0 allows {INTENT_STATUSES}"
            )
        return value

    @field_validator("expiry", "as_of", mode="before")
    @classmethod
    def _dt(cls, value: Any) -> datetime:
        return _parse_dt(value)

    @field_validator("veto_codes", mode="before")
    @classmethod
    def _vetoes(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            token = value.strip()
            return [token] if token else []
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("strategy_id", "card_id", mode="before")
    @classmethod
    def _optional_str(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        token = str(value).strip()
        return token or None

    @model_validator(mode="after")
    def _buy_stop_and_clock(self) -> "Intent":
        if self.side == IntentSide.BUY and self.stop is None:
            raise ValueError("stop is required for side=buy (intent-layer protection, not a broker ticket)")
        if self.expiry <= self.as_of:
            raise ValueError("expiry must be after as_of")
        return self

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        when = _as_utc(now or datetime.now(timezone.utc))
        return _as_utc(self.expiry) <= when

    def is_consumable(self, now: Optional[datetime] = None) -> bool:
        if self.status != IntentStatus.ACTIVE:
            return False
        if self.veto_codes:
            return False
        return not self.is_expired(now)

    def model_dump_json_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


def parse_intent(payload: Union[Dict[str, Any], Intent]) -> Intent:
    """Validate a dict (or Intent) as intent.v0. Raises IntentValidationError."""
    if isinstance(payload, Intent):
        return payload
    try:
        return Intent.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise IntentValidationError(str(exc)) from exc
