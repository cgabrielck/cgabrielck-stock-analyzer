"""
Mandate gate — declarative authorization constraints for automated trading.

A "mandate" is the pre-declared authority under which the worker may trade.
Any order outside the mandate is HARD-REJECTED before it reaches the RiskEngine.
This is a deliberate second, coarser gate: the RiskEngine reasons about
portfolio risk; the mandate reasons about *authorization* (what am I even
allowed to do).

Inspired by Vibe-Trading's mandate-gated live actions.

Config file: repo ``config/mandate.json`` (copy from ``config/mandate.example.json``).
A present, valid document is the policy — including an explicit ``{}`` /
null caps, which means no mandate cap. A missing or unreadable file is not
that policy: ``load_mandate()`` refuses it (fail closed). Desk place-order
uses ``inspect_mandate()`` and fails closed the same way.
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
    """Honest load result for Desk ops, Desk orders, and the worker.

    Missing or unreadable → status missing/corrupt and ``mandate is None``.
    Never substitute a permissive mandate for that. A present valid file,
    including explicit ``{}``, is ``loaded``.
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


def mandate_unavailable_detail(snapshot: MandateSnapshot) -> str:
    """Clear reject text when a mandate file was expected but is not usable.

    Same honesty as Desk place-order: missing and unreadable both fail closed.
    Does not describe an explicit empty ``{}`` file — that file is loaded.
    """
    if snapshot.status == MANDATE_STATUS_MISSING:
        return (
            "Mandate missing — orders fail closed until "
            f"{snapshot.path} is present."
        )
    if snapshot.status == MANDATE_STATUS_CORRUPT:
        return (
            "Mandate corrupt — orders fail closed: "
            f"{snapshot.error or 'unreadable mandate file'}."
        )
    return (
        f"Mandate unusable (status={snapshot.status}) — orders fail closed."
    )


class MandateLoadError(Exception):
    """Mandate file missing or unreadable. No permissive substitute."""

    def __init__(self, snapshot: MandateSnapshot):
        self.snapshot = snapshot
        super().__init__(mandate_unavailable_detail(snapshot))


def load_mandate(path: Optional[Path] = None) -> TradingMandate:
    """
    Load a present mandate file.

    An explicit valid document, including ``{}`` (no caps / no symbol list),
    is returned as written. Missing or unreadable files raise
    ``MandateLoadError`` — callers must fail closed and must not invent a
    permissive mandate. Desk place-order uses ``inspect_mandate()`` and
    rejects the same statuses.
    """
    snap = inspect_mandate(path)
    if snap.is_loaded and snap.mandate is not None:
        return snap.mandate
    logger.error(
        "Mandate %s at %s — fail closed, no permissive fallback: %s",
        snap.status,
        snap.path,
        snap.error,
    )
    raise MandateLoadError(snap)


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


class DailyOrderCountError(Exception):
    """Daily-count file unreadable. Evaluate fail-closes when a cap is set."""


def _parse_stored_date(value: object) -> date:
    """Accept only a calendar ISO date (YYYY-MM-DD). Anything else is corrupt."""
    if not isinstance(value, str):
        raise DailyOrderCountError("daily order count date is not a string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DailyOrderCountError(
            f"daily order count date is not ISO ({value!r})"
        ) from exc
    if value != parsed.isoformat():
        raise DailyOrderCountError(
            f"daily order count date is not ISO ({value!r})"
        )
    return parsed


class DailyOrderCounter:
    """Process-shared UTC-day order count (flock + atomic replace).

    Desk and worker each construct a MandateGate; this file is the shared
    source of truth so a restart cannot reset the day's count.

    ``try_reserve(limit)`` is the atomic check-and-increment used before a
    broker submit so concurrent evaluate-then-record cannot exceed the cap.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path_override = Path(path) if path is not None else None
        self._thread = threading.RLock()

    @property
    def path(self) -> Path:
        if self._path_override is not None:
            return self._path_override
        return Path(DAILY_ORDERS_FILE)

    def _fail_marker(self) -> Path:
        return Path(str(self.path) + ".fail")

    def _mark_unusable(self, exc: BaseException) -> None:
        """Durable fail-closed: next read must not treat the counter as 0."""
        marker = self._fail_marker()
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
        except OSError:
            logger.error("Could not persist daily-count failure marker: %s", exc)

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
        if self._fail_marker().exists():
            raise DailyOrderCountError("daily order counter write previously failed")
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
        if "date" not in data:
            raise DailyOrderCountError("daily order count missing date")
        stored = _parse_stored_date(data.get("date"))
        if stored != today:
            return 0
        if "count" not in data:
            raise DailyOrderCountError("daily order count missing count")
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

    def _locked_write(self, today: date, mutator) -> int:
        with self._exclusive():
            try:
                result = mutator(self._read_unlocked(today))
                self._write_unlocked(today, result)
                return result
            except DailyOrderCountError:
                raise
            except OSError as exc:
                self._mark_unusable(exc)
                raise DailyOrderCountError(str(exc)) from exc

    def get_today(self) -> int:
        today = _utc_today()
        with self._exclusive():
            return self._read_unlocked(today)

    def increment(self) -> int:
        return self._locked_write(_utc_today(), lambda used: used + 1)

    def try_reserve(self, limit: int) -> tuple[bool, int]:
        """Under one flock: increment only if used < limit. Returns (ok, used)."""
        today = _utc_today()
        with self._exclusive():
            try:
                used = self._read_unlocked(today)
                if used >= limit:
                    return False, used
                new = used + 1
                self._write_unlocked(today, new)
                return True, new
            except DailyOrderCountError:
                raise
            except OSError as exc:
                self._mark_unusable(exc)
                raise DailyOrderCountError(str(exc)) from exc

    def release(self) -> int:
        """Give back a reserved slot (risk/broker reject after reserve)."""
        return self._locked_write(_utc_today(), lambda used: max(0, used - 1))


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
        unavailable: Optional[MandateSnapshot] = None,
    ):
        # Explicit ``TradingMandate`` (including ``permissive()`` / ``{}``)
        # is a real policy. ``unavailable`` is a missing or unreadable file:
        # do not install a fake permissive mandate in its place.
        self.unavailable_snapshot: Optional[MandateSnapshot] = None
        self.unavailable_reason: Optional[str] = None
        if unavailable is not None and not unavailable.is_loaded:
            self.unavailable_snapshot = unavailable
            self.unavailable_reason = mandate_unavailable_detail(unavailable)
            self.mandate: Optional[TradingMandate] = None
        else:
            self.mandate = mandate if mandate is not None else TradingMandate.permissive()
        self._counter = DailyOrderCounter(daily_count_path)

    def record_order(self) -> None:
        """Count an accepted BUY order toward the daily limit (durable).

        Prefer ``try_reserve`` before a broker submit when a cap is set —
        that path is atomic. When a cap is set this will not increment
        past it (so evaluate-then-record races cannot write count=cap+1).
        A write failure poisons the store so the next capped evaluate
        fail-closes instead of under-counting.
        """
        if self.mandate is None:
            logger.error(
                "Not counting an order: mandate is not loaded (%s)",
                self.unavailable_reason,
            )
            return
        cap = self.mandate.max_daily_orders
        try:
            if cap is not None:
                self._counter.try_reserve(cap)
            else:
                self._counter.increment()
        except DailyOrderCountError as exc:
            logger.error("Failed to persist daily order count: %s", exc)

    def try_reserve(self) -> MandateDecision:
        """Atomic check-and-increment when ``max_daily_orders`` is set.

        Call this after mandate/risk peek-approve and **before** broker
        submit so two processes cannot both pass evaluate then both record.
        No cap → approved without increment (caller may ``record_order``).
        Missing/unreadable mandate → fail closed, and the daily counter
        file is not touched.
        """
        if self.unavailable_reason or self.mandate is None:
            return MandateDecision(
                approved=False,
                reason=self.unavailable_reason
                or "Mandate not loaded — orders fail closed.",
            )
        cap = self.mandate.max_daily_orders
        if cap is None:
            return MandateDecision(approved=True)
        try:
            ok, used = self._counter.try_reserve(cap)
        except DailyOrderCountError as exc:
            return MandateDecision(
                approved=False,
                reason=f"Daily order counter unreadable — fail closed ({exc}).",
            )
        if not ok:
            return MandateDecision(
                approved=False,
                reason=(f"Daily order limit reached ({used}/{cap})."),
            )
        return MandateDecision(approved=True)

    def release_reservation(self) -> None:
        """Return a slot if we reserved then did not accept the order."""
        if self.mandate is None or self.mandate.max_daily_orders is None:
            return
        try:
            self._counter.release()
        except DailyOrderCountError as exc:
            logger.error("Failed to release daily order slot: %s", exc)

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
        # File missing/unreadable is not a loaded policy. Reject before the
        # SELL-bypass and before any permissive default. An explicit ``{}``
        # never sets ``unavailable_reason``.
        if self.unavailable_reason or self.mandate is None:
            return MandateDecision(
                approved=False,
                reason=self.unavailable_reason
                or "Mandate not loaded — orders fail closed.",
            )
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
