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


def _build_worker(execution_mode="paper", mandate=None, account=None):
    broker = MagicMock()
    store = InMemoryOrderStore()
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
