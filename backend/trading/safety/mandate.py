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

import fcntl
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from backend.trading.models import Order, OrderSide
from backend.trading.risk.gates import positive_price
from backend.utils.constants import DATA_DIR

logger = logging.getLogger(__name__)

# Canonical path is repo-root config/ (next to mandate.example.json).
# Fallback: backend/config for writable VPS layouts that copy the file there.
_REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_MANDATE_FILE = _REPO_ROOT / "config" / "mandate.json"
BACKEND_MANDATE_FILE = Path(DATA_DIR).parent / "config" / "mandate.json"
MANDATE_FILE = REPO_MANDATE_FILE

# Durable UTC-day BUY count shared by Desk and worker (R4). Same DATA_DIR
# convention as kill switch / IntentStore. Missing file = 0 for the day.
DAILY_ORDERS_FILE = Path(DATA_DIR) / "mandate_daily_orders.json"


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


MANDATE_STATUS_LOADED = "loaded"
MANDATE_STATUS_MISSING = "missing"
MANDATE_STATUS_CORRUPT = "corrupt"


class MandateSnapshot(BaseModel):
    """Honest load result for Desk ops + order path (same source).

    Worker ``load_mandate()`` stays fail-open for legacy deploys. Desk
    place-order is fail-closed when status is missing or corrupt.
    """

    status: str
    path: str
    mandate: Optional[TradingMandate] = None
    error: Optional[str] = None

    @property
    def is_loaded(self) -> bool:
        return self.status == MANDATE_STATUS_LOADED and self.mandate is not None


def inspect_mandate(path: Optional[Path] = None) -> MandateSnapshot:
    """Load mandate with an honest status. Never invents a fake loaded file.

    Missing or unreadable files return status missing/corrupt and mandate=None.
    A present, valid file (including an explicit permissive ``{}``) is loaded.
    """
    target = resolve_mandate_path(path)
    target_s = str(target)
    if not target.exists():
        return MandateSnapshot(
            status=MANDATE_STATUS_MISSING,
            path=target_s,
            mandate=None,
            error="mandate file not found",
        )
    try:
        with open(target, "r") as f:
            data = json.load(f)
        mandate = TradingMandate.model_validate(data)
        return MandateSnapshot(
            status=MANDATE_STATUS_LOADED,
            path=target_s,
            mandate=mandate,
            error=None,
        )
    except Exception as exc:
        return MandateSnapshot(
            status=MANDATE_STATUS_CORRUPT,
            path=target_s,
            mandate=None,
            error=str(exc),
        )


def load_mandate(path: Optional[Path] = None) -> TradingMandate:
    """
    Load the mandate from config/mandate.json.

    Worker / legacy path: falls back to a permissive mandate if the file is
    absent or malformed (malformed logs a warning — we fail *open* to preserve
    existing worker behaviour, but the kill switch and RiskEngine remain as
    safety nets). Desk place-order must use ``inspect_mandate()`` and fail closed.
    """
    snap = inspect_mandate(path)
    if snap.is_loaded:
        return snap.mandate  # type: ignore[return-value]
    if snap.status == MANDATE_STATUS_CORRUPT:
        logger.warning(
            "Failed to load mandate from %s: %s — using permissive default.",
            snap.path,
            snap.error,
        )
    return TradingMandate.permissive()


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


class DailyOrderCountError(Exception):
    """Daily-count file unreadable. Evaluate fail-closes when a cap is set."""


class DailyOrderCounter:
    """Process-shared UTC-day order count (flock + atomic replace).

    Desk and worker each construct a MandateGate; this file is the shared
    source of truth so a restart cannot reset the day's count.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path_override = Path(path) if path is not None else None
        self._thread = threading.RLock()

    @property
    def path(self) -> Path:
        if self._path_override is not None:
            return self._path_override
        return Path(DAILY_ORDERS_FILE)

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        lock_path = str(self.path) + ".lock"
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        with self._thread:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _read_unlocked(self, today: date) -> int:
        target = self.path
        if not target.exists():
            return 0
        try:
            with open(target, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise DailyOrderCountError(str(exc)) from exc
        if not isinstance(data, dict):
            raise DailyOrderCountError("daily order count file is not an object")
        stored_date = data.get("date")
        if stored_date != today.isoformat():
            return 0
        raw = data.get("count")
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise DailyOrderCountError("daily order count is not an integer")
        if raw < 0:
            raise DailyOrderCountError("daily order count is negative")
        return raw

    def _write_unlocked(self, today: date, count: int) -> None:
        target = self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"date": today.isoformat(), "count": count, "tz": "UTC"}
        tmp = Path(str(target) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)

    def get_today(self) -> int:
        today = _utc_today()
        with self._exclusive():
            return self._read_unlocked(today)

    def increment(self) -> int:
        today = _utc_today()
        with self._exclusive():
            count = self._read_unlocked(today) + 1
            self._write_unlocked(today, count)
            return count


class MandateGate:
    """
    Enforces a TradingMandate against orders, tracking per-day order counts.

    The daily BUY counter is durable under ``DATA_DIR`` and shared by every
    MandateGate that uses the default path (Desk + worker). UTC day roll is
    on read; a process restart does not reset today's count.
    """

    def __init__(
        self,
        mandate: Optional[TradingMandate] = None,
        *,
        daily_count_path: Optional[Path] = None,
    ):
        self.mandate = mandate or TradingMandate.permissive()
        self._counter = DailyOrderCounter(daily_count_path)

    def record_order(self) -> None:
        """Count an accepted BUY order toward the daily limit (durable)."""
        try:
            self._counter.increment()
        except DailyOrderCountError as exc:
            # Order may already be accepted; next evaluate fail-closes if
            # the file is unreadable. Do not crash Desk/worker after submit.
            logger.error("Failed to persist daily order count: %s", exc)

    @property
    def orders_today(self) -> int:
        return self._counter.get_today()

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
        raw_price = ref_price if ref_price is not None else order.limit_price
        price = positive_price(raw_price)
        if m.max_notional_per_order is not None and price is not None:
            notional = order.quantity * price
            if notional > m.max_notional_per_order:
                return MandateDecision(
                    approved=False,
                    reason=(f"Order notional ${notional:,.0f} exceeds mandate cap "
                            f"${m.max_notional_per_order:,.0f}."),
                )

        # Constraint 5: max daily orders (shared durable counter; fail closed)
        if m.max_daily_orders is not None:
            try:
                orders_today = self.orders_today
            except DailyOrderCountError as exc:
                return MandateDecision(
                    approved=False,
                    reason=(
                        "Daily order counter unreadable — fail closed "
                        f"({exc})."
                    ),
                )
            if orders_today >= m.max_daily_orders:
                return MandateDecision(
                    approved=False,
                    reason=(f"Daily order limit reached ({orders_today}/"
                            f"{m.max_daily_orders})."),
                )

        return MandateDecision(approved=True)
