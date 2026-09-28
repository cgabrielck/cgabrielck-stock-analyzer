"""
Mandate gate — declarative authorization constraints for automated trading.

A "mandate" is the pre-declared authority under which the worker may trade.
Any order outside the mandate is HARD-REJECTED before it reaches the RiskEngine.
This is a deliberate second, coarser gate: the RiskEngine reasons about
portfolio risk; the mandate reasons about *authorization* (what am I even
allowed to do).

Inspired by Vibe-Trading's mandate-gated live actions.

Config file: repo ``config/mandate.json`` (copy from ``config/mandate.example.json``).
If absent, a permissive default is used so older deploys keep running — Wave 1
lands the tightened file locally. Malformed JSON still fails *open* (kill switch
and RiskEngine remain the safety nets).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from backend.trading.models import Order, OrderSide
from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

# Canonical path is repo-root config/ (next to mandate.example.json).
# Fallback: backend/config for writable VPS layouts that copy the file there.
_REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_MANDATE_FILE = _REPO_ROOT / "config" / "mandate.json"
BACKEND_MANDATE_FILE = Path(DATA_DIR).parent / "config" / "mandate.json"
MANDATE_FILE = REPO_MANDATE_FILE


def resolve_mandate_path(path: Optional[Path] = None) -> Path:
    """Prefer repo config/mandate.json, then backend/config/mandate.json."""
    if path is not None:
        return Path(path)
    if REPO_MANDATE_FILE.exists():
        return REPO_MANDATE_FILE
    if BACKEND_MANDATE_FILE.exists():
        return BACKEND_MANDATE_FILE
    return REPO_MANDATE_FILE


class MandateDecision(BaseModel):
    approved: bool
    reason: Optional[str] = None


class TradingMandate(BaseModel):
    """
    Declarative authorization envelope.

    Empty / None fields mean "no constraint" so a default mandate is fully
    permissive and preserves existing behaviour.
    """
    model_config = ConfigDict(extra="ignore")

    # If set, only these symbols may be traded (BUY). Empty = allow all.
    allowed_symbols: List[str] = []
    # If set, these symbols are never tradable (overrides allowed_symbols).
    blocked_symbols: List[str] = []
    # Max notional ($) per single order. None = no cap (RiskEngine still caps).
    max_notional_per_order: Optional[float] = None
    # Max number of BUY orders per calendar day (UTC). None = no cap.
    max_daily_orders: Optional[int] = None
    # Which sides the worker may submit. Default: both.
    allowed_sides: List[str] = ["buy", "sell"]

    @field_validator("allowed_symbols", "blocked_symbols", mode="before")
    @classmethod
    def _upper_tickers(cls, value: Optional[List[str]]) -> List[str]:
        if not value:
            return []
        out: List[str] = []
        for item in value:
            token = str(item or "").upper().strip()
            if token and token not in out:
                out.append(token)
        return out

    @field_validator("allowed_sides", mode="before")
    @classmethod
    def _lower_sides(cls, value: Optional[List[str]]) -> List[str]:
        if not value:
            return ["buy", "sell"]
        return [str(s).lower().strip() for s in value if str(s).strip()]

    @classmethod
    def permissive(cls) -> "TradingMandate":
        """A mandate that constrains nothing (legacy behaviour)."""
        return cls()


def load_mandate(path: Optional[Path] = None) -> TradingMandate:
    """
    Load the mandate from config/mandate.json.

    Falls back to a permissive mandate if the file is absent or malformed
    (malformed logs a warning — we fail *open* to preserve existing behaviour,
    but the kill switch and RiskEngine remain as safety nets).
    """
    target = resolve_mandate_path(path)
    if not target.exists():
        return TradingMandate.permissive()
    try:
        with open(target, "r") as f:
            data = json.load(f)
        return TradingMandate.model_validate(data)
    except Exception as exc:
        logger.warning("Failed to load mandate from %s: %s — using permissive default.", target, exc)
        return TradingMandate.permissive()


class MandateGate:
    """
    Enforces a TradingMandate against orders, tracking per-day order counts.

    Stateful only for the daily-order counter (reset each UTC day).
    """

    def __init__(self, mandate: Optional[TradingMandate] = None):
        self.mandate = mandate or TradingMandate.permissive()
        self._order_count_date: Optional[date] = None
        self._orders_today: int = 0

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self._order_count_date != today:
            self._order_count_date = today
            self._orders_today = 0

    def record_order(self) -> None:
        """Count an accepted BUY order toward the daily limit."""
        self._roll_day()
        self._orders_today += 1

    @property
    def orders_today(self) -> int:
        self._roll_day()
        return self._orders_today

    def evaluate(self, order: Order, ref_price: Optional[float] = None) -> MandateDecision:
        """
        Check *order* against the mandate. Returns MandateDecision.

        Args:
            order:     The draft order.
            ref_price: Reference price for notional check (limit_price used if None).
        """
        m = self.mandate
        side = order.side.value if isinstance(order.side, OrderSide) else str(order.side)

        # Constraint 1: allowed sides
        if side not in m.allowed_sides:
            return MandateDecision(approved=False,
                                   reason=f"Mandate forbids '{side}' orders.")

        # SELL orders (position exits) bypass symbol/notional/daily gates:
        # you must always be able to close a position.
        if side == "sell":
            return MandateDecision(approved=True)

        symbol = str(order.symbol or "").upper().strip()

        # Constraint 2: blocked symbols (hard deny, overrides allow-list)
        if symbol in m.blocked_symbols:
            return MandateDecision(approved=False,
                                   reason=f"{order.symbol} is on the mandate block-list.")

        # Constraint 3: allow-list (if non-empty, symbol must be present)
        if m.allowed_symbols and symbol not in m.allowed_symbols:
            return MandateDecision(approved=False,
                                   reason=f"{order.symbol} is not on the mandate allow-list.")

        # Constraint 4: max notional per order
        price = ref_price if ref_price is not None else order.limit_price
        if m.max_notional_per_order is not None and price is not None:
            notional = order.quantity * price
            if notional > m.max_notional_per_order:
                return MandateDecision(
                    approved=False,
                    reason=(f"Order notional ${notional:,.0f} exceeds mandate cap "
                            f"${m.max_notional_per_order:,.0f}."),
                )

        # Constraint 5: max daily orders
        if m.max_daily_orders is not None and self.orders_today >= m.max_daily_orders:
            return MandateDecision(
                approved=False,
                reason=(f"Daily order limit reached ({self.orders_today}/"
                        f"{m.max_daily_orders})."),
            )

        return MandateDecision(approved=True)
