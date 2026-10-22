"""S1 STAMP parent: worker _submit_sell must evaluate before approve/submit."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.trading.engine.order_manager import InMemoryOrderStore, OrderManager
from backend.trading.engine.shadow import ShadowTradingEngine
from backend.trading.engine.worker import TradingWorker
from backend.trading.models import AccountSummary, OrderSide, OrderStatus, Position
from backend.trading.risk.gates import RiskEngine, RiskLimits
from backend.trading.safety import audit
from backend.trading.safety.mandate import MandateGate, TradingMandate
from backend.utils.provenance import journal_store


def _account(**kwargs):
    payload = dict(
        cash=50_000.0,
        buying_power=50_000.0,
        portfolio_value=100_000.0,
        positions=[
            Position(
                id="p1",
                symbol="AAPL",
                quantity=100.0,
                average_entry_price=90.0,
                current_price=100.40,
            )
        ],
    )
    payload.update(kwargs)
    return AccountSummary(**payload)


def _build_worker(execution_mode="paper", mandate=None, account=None, store=None):
    broker = MagicMock()
    store = store if store is not None else InMemoryOrderStore()
    manager = OrderManager(broker=broker, store=store)
    shadow = ShadowTradingEngine(manager) if execution_mode == "shadow" else None
    worker = TradingWorker(
        broker=broker,
        store=store,
        manager=manager,
        reconciler=MagicMock(),
        strategy_id="stable",
        ticker_universe=["AAPL"],
        mandate=mandate or TradingMandate(max_notional_per_order=10_000.0),
        execution_mode=execution_mode,
        shadow_engine=shadow,
    )
    worker.risk_engine = RiskEngine(RiskLimits(max_order_notional=10_000.0))
    snapshot = account or _account()
    broker.get_account_summary.return_value = snapshot

    def _submit(order):
        order.status = OrderStatus.SUBMITTED
        order.id = "broker-sell"
        return order

    broker.submit_order.side_effect = _submit
    return worker, broker, store


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    journal = tmp_path / "trade_journal.json"
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    yield {"audit_dir": audit_dir, "journal": journal}


def test_sell_502_then_later_call_does_not_mint_a_second_broker_key():
    """Pre-fix _submit_sell minted sell-SYMBOL-timestamp, so a later exit resubmitted."""
    worker, broker, store = _build_worker()
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        err = RuntimeError("504 Gateway Timeout")
        err.status_code = 504
        raise err

    broker.submit_order.side_effect = _submit
    first = worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert first is not None
    assert first.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert first.status != OrderStatus.FILLED
    assert first.idempotency_sticky is True
    second = worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert second is not None
    assert len(set(keys)) == 1
    assert second.idempotency_key == first.idempotency_key
    assert second.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert "do not blind-retry" in (second.error_message or "").lower()


def test_sell_504_then_422_unique_does_not_let_a_new_key_hit_broker():
    """5f21c71 marked the same-key 422 rejected, then sell-AAPL-<ts> hit the broker."""
    worker, broker, store = _build_worker()
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        if keys.count(order.idempotency_key) == 1:
            err = RuntimeError("504 Gateway Timeout")
            err.status_code = 504
            raise err
        err = RuntimeError('{"code":42210000,"message":"client_order_id must be unique"}')
        err.status_code = 422
        raise err

    broker.submit_order.side_effect = _submit
    first = worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert first.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert first.idempotency_sticky is True
    second = worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert second.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert second.status != OrderStatus.REJECTED
    assert second.idempotency_sticky is True
    assert second.idempotency_key == first.idempotency_key
    assert second.filled_avg_price is None
    assert "do not blind-retry" in (second.error_message or "").lower()

    from backend.trading.models import Order, OrderType

    before = broker.submit_order.call_count
    fresh = worker.manager.submit_new_order(
        Order(
            id="sell-fresh",
            symbol="AAPL",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=1,
            limit_price=100.0,
            status=OrderStatus.RISK_APPROVED,
            idempotency_key="sell-AAPL-fresh",
        )
    )
    assert broker.submit_order.call_count == before
    assert fresh.idempotency_key == first.idempotency_key
    assert "sell-AAPL-fresh" not in keys
    assert set(keys) == {first.idempotency_key}
    saved = store.get_order_by_idempotency_key(first.idempotency_key)
    assert saved is not None
    assert saved.idempotency_sticky is True
    assert saved.status == OrderStatus.ACCEPTANCE_UNKNOWN


def test_postgres_422_id_remap_pins_sticky_and_blocks_new_sell_key(postgres_store):
    """Conflict-on-id used to drop the pin when 422 lookup rewrote the local id.

    The next worker sell then minted sell-AAPL-<ts> and hit the broker, and
    the failed save could be stored as rejected.
    """
    worker, broker, store = _build_worker(store=postgres_store)
    keys = []

    def _submit(order):
        keys.append(order.idempotency_key)
        order.id = "broker-sell-landed"
        order.status = OrderStatus.ACCEPTANCE_UNKNOWN
        order.idempotency_sticky = True
        order.error_message = '{"code":42210000,"message":"client_order_id must be unique"}'
        order.filled_avg_price = None
        order.filled_at = None
        return order

    broker.submit_order.side_effect = _submit
    first = worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert first is not None
    assert first.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert first.status != OrderStatus.REJECTED
    assert first.status != OrderStatus.FILLED
    assert first.idempotency_sticky is True
    assert first.id == "broker-sell-landed"
    assert first.filled_avg_price is None
    saved = store.get_order_by_idempotency_key(first.idempotency_key)
    assert saved is not None
    assert saved.id == "broker-sell-landed"
    assert saved.idempotency_sticky is True
    assert saved.status == OrderStatus.ACCEPTANCE_UNKNOWN
    rows = store.iter_all_orders()
    assert len(rows) == 1
    assert rows[0].id == "broker-sell-landed"

    second = worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert second.idempotency_key == first.idempotency_key
    assert second.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert second.status != OrderStatus.REJECTED
    assert second.idempotency_sticky is True
    assert set(keys) == {first.idempotency_key}

    from backend.trading.models import Order, OrderType

    before = broker.submit_order.call_count
    fresh = worker.manager.submit_new_order(
        Order(
            id="sell-AAPL-later",
            symbol="AAPL",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=1,
            limit_price=100.0,
            status=OrderStatus.RISK_APPROVED,
            idempotency_key="sell-AAPL-later",
        )
    )
    assert broker.submit_order.call_count == before
    assert "sell-AAPL-later" not in keys
    assert fresh.idempotency_key == first.idempotency_key
    assert fresh.status == OrderStatus.ACCEPTANCE_UNKNOWN
    assert fresh.status != OrderStatus.FILLED
    assert fresh.idempotency_sticky is True


def test_sell_journal_late_success_still_blocks_a_second_broker_submit(monkeypatch):
    calls = {"n": 0}

    def _journal(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk full")
        return {"id": "journal-ok"}

    monkeypatch.setattr("backend.utils.provenance.journal_store.record_from_order", _journal)
    worker, broker, _store = _build_worker()
    with pytest.raises(Exception):
        worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert broker.submit_order.call_count == 1
    caught_up = worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert caught_up is not None
    assert caught_up.idempotency_sticky is True
    assert broker.submit_order.call_count == 1
    later = worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert later.idempotency_key == caught_up.idempotency_key
    assert broker.submit_order.call_count == 1


def test_sell_journal_502_then_later_call_does_not_second_submit(monkeypatch):
    worker, broker, store = _build_worker()

    def _boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("backend.utils.provenance.journal_store.record_from_order", _boom)
    with pytest.raises(Exception):
        worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert broker.submit_order.call_count == 1
    with pytest.raises(Exception):
        worker._submit_sell("AAPL", 1.0, 100.0, "stop")
    assert broker.submit_order.call_count == 1


def test_paper_sell_honest_notional_over_cap_does_not_hit_broker():
    """qty=100 × $100.40 = $10,040. Haircut 0.995 would be $9,989.80 and sneak under $10k."""
    worker, broker, store = _build_worker()
    result = worker._submit_sell("AAPL", 100.0, 100.40, "stop")
    assert result is not None
    assert result.status == OrderStatus.REJECTED
    assert result.status != OrderStatus.RISK_APPROVED
    assert "exceeds limit" in (result.error_message or "").lower()
    broker.submit_order.assert_not_called()
    saved = store.get_order(result.id)
    assert saved is not None
    assert saved.status == OrderStatus.REJECTED


def test_paper_sell_zero_price_does_not_hit_broker():
    worker, broker, _store = _build_worker()
    result = worker._submit_sell("AAPL", 10.0, 0.0, "stop")
    assert result.status == OrderStatus.REJECTED
    assert "positive reference price" in (result.error_message or "")
    broker.submit_order.assert_not_called()


def test_paper_sell_negative_price_does_not_hit_broker():
    worker, broker, _store = _build_worker()
    result = worker._submit_sell("AAPL", 10.0, -5.0, "stop")
    assert result.status == OrderStatus.REJECTED
    broker.submit_order.assert_not_called()


def test_paper_sell_account_missing_does_not_hit_broker():
    worker, broker, _store = _build_worker()
    broker.get_account_summary.side_effect = RuntimeError("broker down")
    result = worker._submit_sell("AAPL", 10.0, 100.0, "stop")
    assert result.status == OrderStatus.REJECTED
    assert "account snapshot failed" in (result.error_message or "")
    broker.submit_order.assert_not_called()


def test_paper_sell_mandate_forbids_sell_does_not_hit_broker():
    worker, broker, _store = _build_worker(
        mandate=TradingMandate(allowed_sides=["buy"])
    )
    result = worker._submit_sell("AAPL", 10.0, 100.0, "stop")
    assert result.status == OrderStatus.REJECTED
    assert "sell" in (result.error_message or "").lower()
    broker.submit_order.assert_not_called()


def test_shadow_sell_over_cap_is_rejected_not_filled():
    worker, broker, store = _build_worker(execution_mode="shadow")
    worker._fetch_next_bar = lambda *a, **k: None
    result = worker._submit_sell("AAPL", 100.0, 100.40, "stop")
    assert result.status == OrderStatus.REJECTED
    assert result.status != OrderStatus.FILLED
    assert result.status != OrderStatus.RISK_APPROVED
    broker.submit_order.assert_not_called()
    assert all(o.status != OrderStatus.FILLED for o in store.get_recent_orders())


def test_paper_sell_evaluate_then_submit_uses_honest_limit():
    worker, broker, _store = _build_worker()
    risk_calls = []
    mandate_calls = []
    orig_risk = worker.risk_engine.evaluate_order
    orig_man = worker.mandate_gate.evaluate

    def spy_risk(order, account, **kwargs):
        risk_calls.append(order.limit_price)
        return orig_risk(order, account, **kwargs)

    def spy_man(order, ref=None):
        mandate_calls.append(ref)
        return orig_man(order, ref)

    worker.risk_engine.evaluate_order = spy_risk
    worker.mandate_gate.evaluate = spy_man

    result = worker._submit_sell("AAPL", 10.0, 100.40, "take-profit")
    assert result.status == OrderStatus.SUBMITTED
    broker.submit_order.assert_called_once()
    sent = broker.submit_order.call_args[0][0]
    assert sent.limit_price == 100.40
    assert sent.side == OrderSide.SELL
    assert risk_calls == [100.40]
    assert mandate_calls == [100.40]
    assert sent.limit_price != pytest.approx(100.40 * 0.995, abs=1e-6)


def test_shadow_sell_honest_path_evaluates_before_fill():
    worker, broker, _store = _build_worker(execution_mode="shadow")
    worker._fetch_next_bar = lambda *a, **k: None
    risk_calls = []
    orig_risk = worker.risk_engine.evaluate_order
    worker.risk_engine.evaluate_order = lambda *a, **k: risk_calls.append(1) or orig_risk(*a, **k)
    result = worker._submit_sell("AAPL", 10.0, 100.40, "stop")
    assert risk_calls
    assert result.status == OrderStatus.FILLED
    broker.submit_order.assert_not_called()


def test_blocked_sell_does_not_record_fake_exit():
    worker, broker, _store = _build_worker(
        mandate=TradingMandate(allowed_sides=["buy"])
    )
    class _Exit:
        reason = "stop"
        exit_price = 100.40

    worker.strategy.check_exit = lambda **kwargs: _Exit()
    account = _account()
    worker._safe_get_account = lambda: account
    worker._detect_regime = lambda: {"regime": "neutral", "target_allocation": 0.70}
    worker._fetch_vix = lambda: None
    import pandas as pd

    n = 40
    df = pd.DataFrame(
        {
            "Open": [100.0] * n,
            "High": [101.0] * n,
            "Low": [99.0] * n,
            "Close": [100.40] * n,
            "Volume": [1_000_000] * n,
        },
        index=pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n),
    )
    worker._fetch_ohlcv = lambda *a, **k: df
    worker._get_fundamental_score_info = lambda t: {"score": 80.0, "source": "cache", "stale": False}
    worker._run_strategy_signals(account)
    actions = {row.get("action") for row in worker.last_signal_summary}
    assert "EXIT_BLOCKED" in actions
    assert not any(str(a).startswith("EXIT:") for a in actions)
    broker.submit_order.assert_not_called()
    assert "AAPL" not in worker._cooldown
