"""Tests for mandate.py — order authorization and daily limits."""
import json
import threading
from datetime import date

import pytest
from backend.trading.safety.mandate import (
    DailyOrderCounter,
    MandateGate,
    TradingMandate,
)
from backend.trading.models import Order, OrderSide, OrderType


def make_order(symbol="AAPL", side=OrderSide.BUY, quantity=10, limit_price=150.0, oid="test"):
    """Build a minimal valid Order (extra fields default via pydantic)."""
    return Order(
        id=oid,
        symbol=symbol,
        side=side,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        limit_price=limit_price,
        idempotency_key=oid,
    )


@pytest.fixture
def gate():
    """Fresh MandateGate with a 10-order/day, $50K-per-order mandate."""
    mandate = TradingMandate(
        max_daily_orders=10,
        max_notional_per_order=50_000.0,
    )
    return MandateGate(mandate=mandate)


def test_approve_within_limits(gate):
    """Orders within daily and per-order limits are approved."""
    decision = gate.evaluate(make_order(quantity=10, limit_price=150.0), ref_price=150.0)
    assert decision.approved


def test_reject_excessive_notional(gate):
    """Block orders exceeding max_notional_per_order."""
    # 1000 × $1000 = $1M (exceeds $50K)
    order = make_order(symbol="TSLA", quantity=1000, limit_price=1000.0, oid="big")
    decision = gate.evaluate(order, ref_price=1000.0)
    assert not decision.approved
    assert "notional" in (decision.reason or "").lower()


def test_reject_after_daily_limit(gate):
    """Block new BUY orders once the daily limit is hit."""
    for _ in range(10):
        gate.record_order()
    decision = gate.evaluate(make_order(quantity=1, limit_price=100.0), ref_price=100.0)
    assert not decision.approved
    assert "daily order limit" in (decision.reason or "").lower()


def test_sell_orders_bypass_limits(gate):
    """SELL orders (exits) are always approved, even past the daily cap."""
    for _ in range(10):
        gate.record_order()
    order = make_order(symbol="AAPL", side=OrderSide.SELL, quantity=100,
                       limit_price=200.0, oid="exit")
    decision = gate.evaluate(order, ref_price=200.0)
    assert decision.approved


def test_blocked_symbol_denied():
    """Symbols on the block-list are hard-denied."""
    mandate = TradingMandate(blocked_symbols=["GME"])
    gate = MandateGate(mandate=mandate)
    decision = gate.evaluate(make_order(symbol="GME"), ref_price=20.0)
    assert not decision.approved
    assert "block" in (decision.reason or "").lower()


def test_allow_list_enforced():
    """If an allow-list is set, symbols not on it are denied."""
    mandate = TradingMandate(allowed_symbols=["AAPL", "MSFT"])
    gate = MandateGate(mandate=mandate)
    assert gate.evaluate(make_order(symbol="AAPL"), ref_price=150.0).approved
    denied = gate.evaluate(make_order(symbol="NVDA"), ref_price=150.0)
    assert not denied.approved
    assert "allow-list" in (denied.reason or "").lower()


def test_forbidden_side_denied():
    """A mandate that only allows 'sell' rejects BUY orders."""
    mandate = TradingMandate(allowed_sides=["sell"])
    gate = MandateGate(mandate=mandate)
    decision = gate.evaluate(make_order(side=OrderSide.BUY), ref_price=150.0)
    assert not decision.approved


def test_record_order_increments_counter(gate):
    """record_order() increments the daily count."""
    assert gate.orders_today == 0
    gate.record_order()
    gate.record_order()
    assert gate.orders_today == 2


def test_permissive_mandate_allows_everything():
    """The default permissive mandate constrains nothing."""
    gate = MandateGate(mandate=TradingMandate.permissive())
    for _ in range(100):
        gate.record_order()
    order = make_order(symbol="BRK.A", quantity=1, limit_price=500_000.0, oid="whale")
    assert gate.evaluate(order, ref_price=500_000.0).approved


def test_ref_price_falls_back_to_limit_price():
    """When ref_price is None, notional uses the order's limit_price."""
    mandate = TradingMandate(max_notional_per_order=1_000.0)
    gate = MandateGate(mandate=mandate)
    # 100 × $50 = $5000 > $1000, using limit_price since ref_price is None
    order = make_order(quantity=100, limit_price=50.0)
    decision = gate.evaluate(order, ref_price=None)
    assert not decision.approved


def test_load_tightened_example_mandate():
    from pathlib import Path

    from backend.trading.safety.mandate import load_mandate
    from backend.utils.constants import STOCK_UNIVERSE

    example = Path(__file__).resolve().parents[3] / "config" / "mandate.example.json"
    mandate = load_mandate(example)
    assert mandate.max_notional_per_order == 10000.0
    assert mandate.max_daily_orders == 5
    assert "GME" in mandate.blocked_symbols
    universe = {row["ticker"].upper() for row in STOCK_UNIVERSE}
    assert universe.issubset(set(mandate.allowed_symbols))
    assert "TSM" in mandate.allowed_symbols
    gate = MandateGate(mandate=mandate)
    assert gate.evaluate(make_order(symbol="TSM"), ref_price=100.0).approved
    denied = gate.evaluate(make_order(symbol="GME"), ref_price=20.0)
    assert not denied.approved


def test_example_comments_do_not_break_load():
    from pathlib import Path

    from backend.trading.safety.mandate import load_mandate

    example = Path(__file__).resolve().parents[3] / "config" / "mandate.example.json"
    mandate = load_mandate(example)
    assert mandate.allowed_sides == ["buy", "sell"]


def test_inspect_mandate_missing_is_not_loaded(tmp_path):
    from backend.trading.safety.mandate import inspect_mandate

    snap = inspect_mandate(tmp_path / "absent.json")
    assert snap.status == "missing"
    assert snap.mandate is None
    assert snap.error


def test_inspect_mandate_corrupt_is_not_loaded(tmp_path):
    from backend.trading.safety.mandate import inspect_mandate

    target = tmp_path / "mandate.json"
    target.write_text("{not-json", encoding="utf-8")
    snap = inspect_mandate(target)
    assert snap.status == "corrupt"
    assert snap.mandate is None


def test_inspect_mandate_loaded_matches_file(tmp_path):
    import json

    from backend.trading.safety.mandate import inspect_mandate

    target = tmp_path / "mandate.json"
    target.write_text(json.dumps({"blocked_symbols": ["GME"], "max_notional_per_order": 1000}), encoding="utf-8")
    snap = inspect_mandate(target)
    assert snap.is_loaded
    assert snap.status == "loaded"
    assert snap.mandate is not None
    assert "GME" in snap.mandate.blocked_symbols
    assert snap.mandate.max_notional_per_order == 1000


def test_load_mandate_missing_file_fail_closed(tmp_path):
    """Missing file must not become a permissive mandate (pre-fix worker hole)."""
    from backend.trading.safety.mandate import MandateLoadError, load_mandate

    with pytest.raises(MandateLoadError) as exc:
        load_mandate(tmp_path / "nope.json")
    assert exc.value.snapshot.status == "missing"
    assert exc.value.snapshot.mandate is None
    assert "fail closed" in str(exc.value).lower()


def test_load_mandate_unreadable_file_fail_closed(tmp_path):
    """Corrupt / unreadable bytes are not a permissive fallback."""
    from backend.trading.safety.mandate import MandateLoadError, load_mandate

    corrupt = tmp_path / "bad.json"
    corrupt.write_text("[]", encoding="utf-8")
    with pytest.raises(MandateLoadError) as exc:
        load_mandate(corrupt)
    assert exc.value.snapshot.status == "corrupt"
    assert exc.value.snapshot.mandate is None
    assert "fail closed" in str(exc.value).lower()

    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(MandateLoadError) as exc_empty:
        load_mandate(empty)
    assert exc_empty.value.snapshot.status == "corrupt"

    unreadable = tmp_path / "mandate-dir.json"
    unreadable.mkdir()
    with pytest.raises(MandateLoadError) as exc_dir:
        load_mandate(unreadable)
    assert exc_dir.value.snapshot.status == "corrupt"
    assert exc_dir.value.snapshot.mandate is None


def test_load_mandate_explicit_empty_object_stays_permissive(tmp_path):
    """``{}`` is an intentional no-cap policy, not a missing file."""
    from backend.trading.safety.mandate import TradingMandate, load_mandate

    target = tmp_path / "mandate.json"
    target.write_text("{}", encoding="utf-8")
    loaded = load_mandate(target)
    assert loaded.model_dump() == TradingMandate.permissive().model_dump()
    assert loaded.max_daily_orders is None
    assert loaded.max_notional_per_order is None


def test_load_mandate_explicit_null_caps_stay_uncapped(tmp_path):
    from backend.trading.safety.mandate import load_mandate

    target = tmp_path / "mandate.json"
    target.write_text(
        '{"max_daily_orders": null, "max_notional_per_order": null, "allowed_symbols": []}',
        encoding="utf-8",
    )
    loaded = load_mandate(target)
    assert loaded.max_daily_orders is None
    assert loaded.max_notional_per_order is None
    assert loaded.allowed_symbols == []


def test_unavailable_gate_rejects_buy_and_sell_without_touching_counter(tmp_path):
    """Missing-file gate is not permissive, and does not write the R4 counter."""
    from pathlib import Path

    from backend.trading.safety.mandate import MandateGate, inspect_mandate

    snap = inspect_mandate(tmp_path / "absent.json")
    counter = tmp_path / "mandate_daily_orders.json"
    gate = MandateGate(unavailable=snap, daily_count_path=counter)
    assert gate.mandate is None
    buy = gate.evaluate(make_order(), ref_price=150.0)
    assert not buy.approved
    assert "fail closed" in (buy.reason or "").lower()
    sell = gate.evaluate(
        make_order(side=OrderSide.SELL, oid="exit"),
        ref_price=150.0,
    )
    assert not sell.approved
    assert "fail closed" in (sell.reason or "").lower()
    reserved = gate.try_reserve()
    assert not reserved.approved
    gate.record_order()
    assert not counter.exists()
    assert not Path(str(counter) + ".fail").exists()


def test_restart_mid_day_still_enforces_daily_limit(tmp_path):
    """R4 fail-case: a new MandateGate (process restart) must not reset the day.

    Pre-fix main kept orders_today in memory; a new gate started at 0.
    """
    store = tmp_path / "daily.json"
    mandate = TradingMandate(max_daily_orders=2)
    first = MandateGate(mandate=mandate, daily_count_path=store)
    first.record_order()
    first.record_order()
    assert first.orders_today == 2
    denied_in_process = first.evaluate(make_order(), ref_price=100.0)
    assert not denied_in_process.approved

    restarted = MandateGate(mandate=mandate, daily_count_path=store)
    assert restarted.orders_today == 2
    denied = restarted.evaluate(make_order(), ref_price=100.0)
    assert not denied.approved
    assert "daily order limit" in (denied.reason or "").lower()


def test_desk_and_worker_gates_share_the_same_daily_count(tmp_path):
    """R4 fail-case: worker record_order counts toward Desk evaluate (and vice versa).

    Pre-fix main: each process had its own memory counter.
    """
    store = tmp_path / "daily.json"
    mandate = TradingMandate(max_daily_orders=1)
    worker = MandateGate(mandate=mandate, daily_count_path=store)
    desk = MandateGate(mandate=mandate, daily_count_path=store)
    worker.record_order()
    denied = desk.evaluate(make_order(symbol="MSFT"), ref_price=100.0)
    assert not denied.approved
    assert "daily order limit" in (denied.reason or "").lower()
    assert desk.orders_today == 1
    assert worker.orders_today == 1


def test_corrupt_daily_counter_fail_closed_when_cap_set(tmp_path):
    store = tmp_path / "daily.json"
    store.write_text("{not-json", encoding="utf-8")
    gate = MandateGate(
        mandate=TradingMandate(max_daily_orders=5),
        daily_count_path=store,
    )
    decision = gate.evaluate(make_order(), ref_price=100.0)
    assert not decision.approved
    assert "fail closed" in (decision.reason or "").lower()


def test_corrupt_daily_counter_ignored_when_no_cap(tmp_path):
    store = tmp_path / "daily.json"
    store.write_text("{not-json", encoding="utf-8")
    gate = MandateGate(
        mandate=TradingMandate.permissive(),
        daily_count_path=store,
    )
    assert gate.evaluate(make_order(), ref_price=100.0).approved


def test_new_utc_day_resets_durable_count(tmp_path, monkeypatch):
    from backend.trading.safety import mandate as mandate_mod

    store = tmp_path / "daily.json"
    mandate = TradingMandate(max_daily_orders=1)
    monkeypatch.setattr(mandate_mod, "_utc_today", lambda: date(2026, 10, 22))
    day1 = MandateGate(mandate=mandate, daily_count_path=store)
    day1.record_order()
    assert not day1.evaluate(make_order(), ref_price=100.0).approved

    monkeypatch.setattr(mandate_mod, "_utc_today", lambda: date(2026, 10, 23))
    day2 = MandateGate(mandate=mandate, daily_count_path=store)
    assert day2.orders_today == 0
    assert day2.evaluate(make_order(), ref_price=100.0).approved


def test_interleaved_evaluate_then_record_cannot_exceed_cap(tmp_path):
    """R4 fail-case: both paths peek-approve, then only one slot is taken.

    On HEAD 9fe0632, evaluate then record_order on two gates at cap=1
    left count=2 and reason like Daily order limit reached (2/1).
    """
    store = tmp_path / "daily.json"
    mandate = TradingMandate(max_daily_orders=1)
    desk = MandateGate(mandate=mandate, daily_count_path=store)
    worker = MandateGate(mandate=mandate, daily_count_path=store)
    assert desk.evaluate(make_order(oid="d"), ref_price=100.0).approved
    assert worker.evaluate(make_order(oid="w"), ref_price=100.0).approved
    first = desk.try_reserve()
    second = worker.try_reserve()
    assert first.approved is True
    assert second.approved is False
    assert "daily order limit" in (second.reason or "").lower()
    assert "(2/1)" not in (second.reason or "")
    assert desk.orders_today == 1
    assert worker.orders_today == 1
    desk.record_order()
    worker.record_order()
    assert desk.orders_today == 1


def test_concurrent_evaluate_then_reserve_cap_1(tmp_path):
    store = tmp_path / "daily.json"
    mandate = TradingMandate(max_daily_orders=1)
    desk = MandateGate(mandate=mandate, daily_count_path=store)
    worker = MandateGate(mandate=mandate, daily_count_path=store)
    barrier = threading.Barrier(2, timeout=5)
    slots = []

    def run(gate, oid):
        assert gate.evaluate(make_order(oid=oid), ref_price=100.0).approved
        barrier.wait()
        slots.append(gate.try_reserve().approved)

    t1 = threading.Thread(target=run, args=(desk, "d"))
    t2 = threading.Thread(target=run, args=(worker, "w"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert slots.count(True) == 1
    assert slots.count(False) == 1
    assert desk.orders_today == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"count": 5},
        {"date": "not-a-date", "count": 1},
        {"date": "2026-13-40", "count": 1},
        {"date": 20261022, "count": 1},
    ],
)
def test_incomplete_or_bad_date_fail_closed_when_cap_set(tmp_path, payload):
    """R4 fail-case: {}, missing date, bad date must not be treated as 0."""
    store = tmp_path / "daily.json"
    store.write_text(json.dumps(payload), encoding="utf-8")
    gate = MandateGate(
        mandate=TradingMandate(max_daily_orders=5),
        daily_count_path=store,
    )
    decision = gate.evaluate(make_order(), ref_price=100.0)
    assert not decision.approved
    assert "fail closed" in (decision.reason or "").lower()


def test_today_iso_date_missing_count_fail_closed(tmp_path, monkeypatch):
    from backend.trading.safety import mandate as mandate_mod

    store = tmp_path / "daily.json"
    store.write_text(json.dumps({"date": "2026-10-22"}), encoding="utf-8")
    monkeypatch.setattr(mandate_mod, "_utc_today", lambda: date(2026, 10, 22))
    gate = MandateGate(
        mandate=TradingMandate(max_daily_orders=5),
        daily_count_path=store,
    )
    decision = gate.evaluate(make_order(), ref_price=100.0)
    assert not decision.approved
    assert "fail closed" in (decision.reason or "").lower()


def test_prior_day_iso_date_resets_to_zero(tmp_path, monkeypatch):
    from backend.trading.safety import mandate as mandate_mod

    store = tmp_path / "daily.json"
    store.write_text(
        json.dumps({"date": "2026-10-21", "count": 9, "tz": "UTC"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mandate_mod, "_utc_today", lambda: date(2026, 10, 22))
    gate = MandateGate(
        mandate=TradingMandate(max_daily_orders=1),
        daily_count_path=store,
    )
    assert gate.orders_today == 0
    assert gate.evaluate(make_order(), ref_price=100.0).approved


def test_write_failure_after_increment_fail_closes_next_buy(tmp_path, monkeypatch):
    """R4 fail-case: OSError on count write must not leave a hole under the cap.

    Pre-fix: increment failed, count stayed 0, next evaluate still approved.
    """
    store = tmp_path / "daily.json"
    gate = MandateGate(
        mandate=TradingMandate(max_daily_orders=5),
        daily_count_path=store,
    )
    gate.record_order()
    assert gate.orders_today == 1

    def boom(self, today, count):
        raise OSError("disk full")

    monkeypatch.setattr(DailyOrderCounter, "_write_unlocked", boom)
    gate.record_order()
    denied = gate.evaluate(make_order(), ref_price=100.0)
    assert not denied.approved
    assert "fail closed" in (denied.reason or "").lower()
    assert store.with_suffix(store.suffix + ".fail").exists() or (tmp_path / "daily.json.fail").exists()
