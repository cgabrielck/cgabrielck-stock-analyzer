"""H1 consume path: expiry, skip rules, M8 sizing, status transitions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.trading.engine.harness_realtime import (
    TIER_ENVELOPE_SCALE,
    consume_active_intents,
    size_intent_qty,
)
from backend.trading.intents.models import IntentStatus, parse_intent
from backend.trading.intents.store import JSONIntentStore
from backend.trading.risk.gates import RiskLimits

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
EXPIRY = NOW + timedelta(minutes=30)
PAST = NOW - timedelta(minutes=5)


def _store(tmp_path, *payloads) -> JSONIntentStore:
    store = JSONIntentStore(filepath=str(tmp_path / "intents.json"))
    for payload in payloads:
        store.save(parse_intent(payload))
    return store


def _sell(**overrides):
    data = {
        "schema_version": "intent.v0",
        "intent_id": "h1-sell",
        "symbol": "AAPL",
        "side": "sell",
        "stop": None,
        "conviction_tier": "med",
        "expiry": EXPIRY.isoformat(),
        "as_of": NOW.isoformat(),
        "status": "active",
        "strategy_id": "stable",
    }
    data.update(overrides)
    return data


def _buy(**overrides):
    data = {
        "schema_version": "intent.v0",
        "intent_id": "h1-1",
        "symbol": "AAPL",
        "side": "buy",
        "stop": {"type": "pct", "value": 0.10},
        "conviction_tier": "med",
        "expiry": EXPIRY.isoformat(),
        "as_of": NOW.isoformat(),
        "status": "active",
        "strategy_id": "stable",
    }
    data.update(overrides)
    return data


def test_expired_intents_are_marked_and_not_processed(tmp_path):
    store = _store(tmp_path, _buy(intent_id="old", expiry=PAST.isoformat(), as_of=PAST - timedelta(hours=1)))
    calls = []
    results = consume_active_intents(
        store,
        process_signal=lambda *a, **k: calls.append(1),
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=40_000),
        now=NOW,
    )
    assert calls == []
    assert any(row.action == "expired" for row in results)
    assert store.get("old").status is IntentStatus.EXPIRED


def test_non_active_skipped(tmp_path):
    store = _store(tmp_path, _buy(intent_id="done", status="consumed"))
    calls = []
    consume_active_intents(
        store,
        process_signal=lambda *a, **k: calls.append(1),
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=40_000),
        now=NOW,
    )
    assert calls == []
    assert store.get("done").status is IntentStatus.CONSUMED


def test_veto_codes_cancel_without_broker(tmp_path):
    store = _store(tmp_path, _buy(intent_id="veto", veto_codes=["KILL_ACTIVE"]))
    calls = []
    results = consume_active_intents(
        store,
        process_signal=lambda *a, **k: calls.append(1),
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=40_000),
        now=NOW,
    )
    assert calls == []
    assert results[0].action == "cancelled"
    assert store.get("veto").status is IntentStatus.CANCELLED


def test_kill_switch_skips_without_consuming(tmp_path):
    store = _store(tmp_path, _buy(intent_id="alive"))
    calls = []
    results = consume_active_intents(
        store,
        process_signal=lambda *a, **k: calls.append(1),
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=40_000),
        now=NOW,
        killed=True,
    )
    assert calls == []
    assert results[0].action == "skipped_kill"
    assert store.get("alive").status is IntentStatus.ACTIVE


def test_no_price_leaves_active(tmp_path):
    store = _store(tmp_path, _buy(intent_id="px"))
    results = consume_active_intents(
        store,
        process_signal=lambda *a, **k: None,
        get_price=lambda _s: None,
        account_summary=SimpleNamespace(portfolio_value=40_000),
        now=NOW,
    )
    assert results[0].action == "skipped_no_price"
    assert store.get("px").status is IntentStatus.ACTIVE


def test_zero_equity_does_not_hand_to_m9(tmp_path):
    store = _store(tmp_path, _buy(intent_id="eq"))
    calls = []
    results = consume_active_intents(
        store,
        process_signal=lambda *a, **k: calls.append(1),
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=0),
        now=NOW,
    )
    assert calls == []
    assert results[0].action == "skipped_zero_qty"
    assert store.get("eq").status is IntentStatus.ACTIVE


def test_risk_reject_stays_consumed_but_action_is_not_happy(tmp_path):
    from backend.trading.models import OrderStatus

    store = _store(tmp_path, _buy(intent_id="rej"))

    def process_signal(signal, **kwargs):
        return SimpleNamespace(
            id="ord-rej",
            status=OrderStatus.REJECTED,
            error_message="Risk Gate Rejected: Order notional exceeds limit.",
        )

    results = consume_active_intents(
        store,
        process_signal=process_signal,
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=40_000),
        now=NOW,
        limits=RiskLimits(),
    )
    assert store.get("rej").status is IntentStatus.CONSUMED
    assert results[0].action == "consumed_risk_rejected"
    assert results[0].reason == "risk_rejected"
    assert results[0].order_id == "ord-rej"


def test_h1_journal_raise_after_allow_submit_is_not_happy_consumed(tmp_path):
    """H1 → process_signal allow+submit + journal RuntimeError is not silent success."""
    from unittest.mock import MagicMock, patch

    from backend.trading.engine.signal_processor import SignalProcessor
    from backend.trading.models import AccountSummary, OrderStatus
    from backend.trading.risk.gates import RiskDecision

    store = _store(tmp_path, _buy(intent_id="jfail"))
    risk = MagicMock()
    risk.evaluate_order.return_value = RiskDecision(approved=True)
    submitted = MagicMock()
    submitted.status = OrderStatus.SUBMITTED
    submitted.id = "ord-jfail"
    submitted.symbol = "AAPL"
    submitted.error_message = None
    om = MagicMock()
    om.submit_new_order.return_value = submitted
    broker = MagicMock()
    broker.get_account_summary.return_value = AccountSummary(
        cash=40_000.0,
        buying_power=40_000.0,
        portfolio_value=40_000.0,
        positions=[],
    )
    processor = SignalProcessor(broker, risk, om)

    with patch(
        "backend.utils.provenance.journal_store.record_from_order",
        side_effect=RuntimeError("disk full"),
    ):
        results = consume_active_intents(
            store,
            process_signal=processor.process_signal,
            get_price=lambda _s: 150.0,
            account_summary=SimpleNamespace(portfolio_value=40_000, positions=[]),
            now=NOW,
            limits=RiskLimits(),
        )

    assert results[0].action == "consumed_journal_failed"
    assert results[0].action != "consumed"
    assert results[0].reason == "journal_write_failed"
    assert results[0].order_id == "ord-jfail"
    om.submit_new_order.assert_called_once()
    assert store.get("jfail").status is IntentStatus.CONSUMED


def test_one_intent_exception_does_not_block_others(tmp_path, caplog):
    import logging

    store = _store(
        tmp_path,
        _buy(intent_id="boom", symbol="FAIL"),
        _buy(intent_id="ok", symbol="AAPL"),
    )

    def get_price(symbol):
        if symbol == "FAIL":
            raise RuntimeError("quote down")
        return 150.0

    captured = []

    def process_signal(signal, **kwargs):
        captured.append(signal["intent_id"])
        return SimpleNamespace(id="ord-ok")

    with caplog.at_level(logging.ERROR):
        results = consume_active_intents(
            store,
            process_signal=process_signal,
            get_price=get_price,
            account_summary=SimpleNamespace(portfolio_value=40_000),
            now=NOW,
            limits=RiskLimits(),
        )
    by_id = {row.intent_id: row for row in results}
    assert by_id["boom"].action == "skipped_process_error"
    assert store.get("boom").status is IntentStatus.ACTIVE
    assert "ok" in captured
    assert store.get("ok").status is IntentStatus.CONSUMED
    assert any("intent_id=boom" in rec.message for rec in caplog.records)


def test_worker_consume_loop_logs_error_not_debug(caplog):
    import logging
    from unittest.mock import MagicMock

    from backend.trading.engine.worker import TradingWorker

    worker = TradingWorker.__new__(TradingWorker)
    worker.is_halted = False
    worker.intent_store = MagicMock()
    worker.intent_store.list.side_effect = RuntimeError("store down")
    worker.signal_processor = MagicMock()
    worker.risk_engine = MagicMock()
    worker.risk_engine.limits = None

    with caplog.at_level(logging.ERROR, logger="backend.trading.engine.worker"):
        TradingWorker._consume_intents(worker, SimpleNamespace())
    assert any("H1 intent consume loop failed" in rec.message for rec in caplog.records)
    src = Path(__file__).resolve().parents[3] / "backend/trading/engine/worker.py"
    text = src.read_text(encoding="utf-8")
    consume_src = text.split("def _consume_intents")[1].split("def _is_market_hours")[0]
    assert "logger.debug(" not in consume_src
    assert "logger.error(" in consume_src


def test_consumable_intent_sized_by_m8_then_consumed(tmp_path):
    store = _store(tmp_path, _buy(intent_id="go", conviction_tier="high"))
    captured = []

    def process_signal(signal, **kwargs):
        captured.append(signal)
        return SimpleNamespace(id="ord-go")

    results = consume_active_intents(
        store,
        process_signal=process_signal,
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=40_000),
        now=NOW,
        limits=RiskLimits(),
    )
    assert len(captured) == 1
    signal = captured[0]
    assert signal["symbol"] == "AAPL"
    assert signal["quantity"] > 0
    assert signal["intent_id"] == "go"
    assert signal["idempotency_key"] == "intent:go"
    assert "qty" not in store.get("go").model_dump_json_dict()
    assert results[0].action == "consumed"
    assert results[0].order_id == "ord-go"
    assert store.get("go").status is IntentStatus.CONSUMED


def test_tier_scale_is_within_m8_envelope():
    intent_low = parse_intent(_buy(conviction_tier="low"))
    intent_high = parse_intent(_buy(conviction_tier="high"))
    limits = RiskLimits()
    low = size_intent_qty(intent_low, price=100.0, equity=40_000, limits=limits)
    high = size_intent_qty(intent_high, price=100.0, equity=40_000, limits=limits)
    assert TIER_ENVELOPE_SCALE["low"] < TIER_ENVELOPE_SCALE["high"]
    assert 0 < low <= high
    cap_high = int(limits.max_order_notional / 100.0)
    assert high <= cap_high


def test_harness_modules_do_not_import_lab_or_api_or_llm():
    root = Path(__file__).resolve().parents[3]
    files = [
        root / "backend/trading/engine/harness_realtime.py",
        root / "backend/trading/intents/writer.py",
        root / "backend/trading/intents/store.py",
        root / "backend/trading/intents/models.py",
    ]
    banned = (
        "from backend.lab",
        "import backend.api",
        "from backend.api",
        "llm_agent",
        "from backend.trading.engine.order_manager",
        "advisory_debate",
    )
    for path in files:
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} must not reference {token}"


def test_worker_exposes_thin_consume_hook():
    from backend.trading.engine.worker import TradingWorker

    assert callable(getattr(TradingWorker, "_consume_intents", None))
    src = Path(__file__).resolve().parents[3] / "backend/trading/engine/worker.py"
    text = src.read_text(encoding="utf-8")
    assert "consume_active_intents" in text
    assert "TODO(full-h1-h2-split)" in text
    assert "advisory_debate" not in text
    consume_src = text.split("def _consume_intents")[1].split("def _is_market_hours")[0]
    assert "from backend.api" not in consume_src
    assert "mandate_gate" in consume_src
    assert "cooldown_tickers" in consume_src
    assert "price_history" in consume_src
    assert "vix_level" in consume_src
    assert "vix_size_multiplier" in consume_src


def test_mandate_blocked_symbol_does_not_size_or_submit(tmp_path):
    from unittest.mock import patch

    from backend.trading.safety.mandate import MandateGate, TradingMandate

    store = _store(tmp_path, _buy(intent_id="blk", symbol="GME"))
    gate = MandateGate(mandate=TradingMandate(blocked_symbols=["GME"]))
    calls = []
    with patch(
        "backend.trading.engine.harness_realtime.size_intent_qty",
        wraps=size_intent_qty,
    ) as sized:
        results = consume_active_intents(
            store,
            process_signal=lambda *a, **k: calls.append(("signal", a, k)),
            get_price=lambda _s: 150.0,
            account_summary=SimpleNamespace(portfolio_value=40_000, positions=[]),
            now=NOW,
            mandate_gate=gate,
        )
    assert sized.call_count == 0
    assert calls == []
    assert results[0].action == "skipped_mandate"
    assert store.get("blk").status is IntentStatus.ACTIVE


def test_consume_forwards_risk_extras_to_process_signal(tmp_path):
    store = _store(tmp_path, _buy(intent_id="risk-ctx"))
    seen = []

    def process_signal(signal, **kwargs):
        seen.append(kwargs.get("risk_extras"))
        return SimpleNamespace(id="ord-ctx")

    extras = {
        "vix_level": 22.5,
        "price_history": {"AAPL": object()},
        "cooldown_tickers": ["MSFT"],
    }
    consume_active_intents(
        store,
        process_signal=process_signal,
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=40_000, positions=[]),
        now=NOW,
        risk_extras=extras,
    )
    assert seen == [extras]


def test_sell_zero_position_does_not_open_short(tmp_path):
    store = _store(tmp_path, _sell(intent_id="no-short"))
    calls = []
    results = consume_active_intents(
        store,
        process_signal=lambda *a, **k: calls.append(1),
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=100_000, positions=[]),
        now=NOW,
        limits=RiskLimits(),
    )
    assert calls == []
    assert results[0].action == "skipped_no_long"
    assert results[0].qty == 0
    assert store.get("no-short").status is IntentStatus.ACTIVE


def test_sell_flatten_uses_long_qty_not_kelly():
    buy_qty = size_intent_qty(
        parse_intent(_buy()),
        price=150.0,
        equity=100_000,
        limits=RiskLimits(),
    )
    sell_none = size_intent_qty(
        parse_intent(_sell()),
        price=150.0,
        equity=100_000,
        limits=RiskLimits(),
        account_summary=SimpleNamespace(portfolio_value=100_000, positions=[]),
    )
    sell_held = size_intent_qty(
        parse_intent(_sell()),
        price=150.0,
        equity=100_000,
        limits=RiskLimits(),
        account_summary=SimpleNamespace(
            portfolio_value=100_000,
            positions=[SimpleNamespace(symbol="AAPL", quantity=9)],
        ),
    )
    assert buy_qty > 0
    assert sell_none == 0
    assert sell_held == 9
    assert sell_held != buy_qty


def test_sell_with_long_submits_flatten_qty(tmp_path):
    store = _store(tmp_path, _sell(intent_id="flat"))
    captured = []

    def process_signal(signal, **kwargs):
        captured.append(signal)
        return SimpleNamespace(id="ord-flat")

    results = consume_active_intents(
        store,
        process_signal=process_signal,
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(
            portfolio_value=40_000,
            positions=[SimpleNamespace(symbol="AAPL", quantity=12)],
        ),
        now=NOW,
        limits=RiskLimits(),
    )
    assert len(captured) == 1
    assert captured[0]["side"] == "sell"
    assert captured[0]["quantity"] == 12
    assert results[0].action == "consumed"
    assert store.get("flat").status is IntentStatus.CONSUMED


def test_cancel_before_consume_skips_submit(tmp_path):
    store = _store(tmp_path, _buy(intent_id="race-cancel"))
    store.set_status("race-cancel", IntentStatus.CANCELLED)
    calls = []
    results = consume_active_intents(
        store,
        process_signal=lambda *a, **k: calls.append(1),
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=40_000, positions=[]),
        now=NOW,
    )
    assert calls == []
    assert store.get("race-cancel").status is IntentStatus.CANCELLED
    assert all(row.intent_id != "race-cancel" or row.action != "consumed" for row in results)


def test_mandate_daily_cap_counts_each_accepted_buy(tmp_path):
    """max_daily_orders must stick inside one consume loop, same as the strategy scan."""
    from backend.trading.safety.mandate import MandateGate, TradingMandate

    store = _store(
        tmp_path,
        _buy(
            intent_id="day-1",
            symbol="AAPL",
            as_of=(NOW + timedelta(seconds=2)).isoformat(),
        ),
        _buy(
            intent_id="day-2",
            symbol="MSFT",
            as_of=(NOW + timedelta(seconds=1)).isoformat(),
        ),
    )
    gate = MandateGate(mandate=TradingMandate(max_daily_orders=1))
    calls = []

    def process_signal(signal, **kwargs):
        calls.append(signal["symbol"])
        return SimpleNamespace(id=f"ord-{signal['symbol']}")

    results = consume_active_intents(
        store,
        process_signal=process_signal,
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=40_000, positions=[]),
        now=NOW,
        limits=RiskLimits(),
        mandate_gate=gate,
    )
    by_id = {row.intent_id: row for row in results}
    assert calls == ["AAPL"]
    assert gate.orders_today == 1
    assert by_id["day-1"].action == "consumed"
    assert store.get("day-1").status is IntentStatus.CONSUMED
    assert by_id["day-2"].action == "skipped_mandate"
    assert store.get("day-2").status is IntentStatus.ACTIVE
    assert "daily order limit" in (by_id["day-2"].reason or "").lower()


def test_mandate_daily_cap_ignores_sells_and_risk_rejects(tmp_path):
    from backend.trading.models import OrderStatus
    from backend.trading.safety.mandate import MandateGate, TradingMandate

    store = _store(
        tmp_path,
        _sell(
            intent_id="day-sell",
            symbol="AAPL",
            as_of=(NOW + timedelta(seconds=3)).isoformat(),
        ),
        _buy(
            intent_id="day-rej",
            symbol="MSFT",
            as_of=(NOW + timedelta(seconds=2)).isoformat(),
        ),
        _buy(
            intent_id="day-ok",
            symbol="NVDA",
            as_of=(NOW + timedelta(seconds=1)).isoformat(),
        ),
    )
    gate = MandateGate(mandate=TradingMandate(max_daily_orders=1))
    calls = []

    def process_signal(signal, **kwargs):
        calls.append(signal["intent_id"])
        if signal["intent_id"] == "day-rej":
            return SimpleNamespace(
                id="ord-rej",
                status=OrderStatus.REJECTED,
                error_message="Risk Gate Rejected: cooldown",
            )
        return SimpleNamespace(id=f"ord-{signal['intent_id']}")

    results = consume_active_intents(
        store,
        process_signal=process_signal,
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(
            portfolio_value=40_000,
            positions=[SimpleNamespace(symbol="AAPL", quantity=4)],
        ),
        now=NOW,
        limits=RiskLimits(),
        mandate_gate=gate,
    )
    by_id = {row.intent_id: row for row in results}
    assert by_id["day-sell"].action == "consumed"
    assert by_id["day-rej"].action == "consumed_risk_rejected"
    assert by_id["day-ok"].action == "consumed"
    assert calls == ["day-sell", "day-rej", "day-ok"]
    assert gate.orders_today == 1
    assert store.get("day-ok").status is IntentStatus.CONSUMED


def test_consume_claim_beats_cancel_before_process_signal(tmp_path):
    from backend.trading.intents.store import IntentConflictError

    store = _store(tmp_path, _buy(intent_id="race-go"))
    calls = []

    def process_signal(signal, **kwargs):
        with pytest.raises(IntentConflictError):
            store.set_status("race-go", IntentStatus.CANCELLED)
        calls.append(signal)
        return SimpleNamespace(id="ord-go")

    results = consume_active_intents(
        store,
        process_signal=process_signal,
        get_price=lambda _s: 150.0,
        account_summary=SimpleNamespace(portfolio_value=40_000, positions=[]),
        now=NOW,
        limits=RiskLimits(),
    )
    assert calls
    assert results[0].action == "consumed"
    assert store.get("race-go").status is IntentStatus.CONSUMED
