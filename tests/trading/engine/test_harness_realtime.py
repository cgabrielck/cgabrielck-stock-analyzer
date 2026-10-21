"""H1 consume path: expiry, skip rules, M8 sizing, status transitions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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
    assert "from backend.api" not in text.split("def _consume_intents")[1][:1200]
