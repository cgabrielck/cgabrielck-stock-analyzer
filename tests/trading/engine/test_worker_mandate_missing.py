"""R2: a missing or unreadable mandate file must not let a worker order succeed.

On pre-fix main, ``load_mandate()`` returned ``TradingMandate.permissive()``
and the same setup submitted the order. Explicit ``{}`` stays a no-cap policy.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from backend.trading.engine.order_manager import InMemoryOrderStore, OrderManager
from backend.trading.engine.worker import TradingWorker
from backend.trading.models import AccountSummary, OrderStatus, Position
from backend.trading.safety import audit
from backend.trading.safety.mandate import TradingMandate
from backend.trading.strategies.base import Signal
from backend.utils.provenance import journal_store
from backend.utils.provenance.journal_store import load_raw_entries

_SUCCESS = {
    OrderStatus.SUBMITTED,
    OrderStatus.ACCEPTED,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.FILLED,
    OrderStatus.RISK_APPROVED,
}


def _bars(price: float = 100.0, n: int = 40) -> pd.DataFrame:
    close = [price] * n
    return pd.DataFrame(
        {
            "Open": close,
            "High": [price * 1.01] * n,
            "Low": [price * 0.99] * n,
            "Close": close,
            "Volume": [1_000_000] * n,
        }
    )


def _account(**kwargs) -> AccountSummary:
    payload = dict(
        cash=100_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
        positions=[],
    )
    payload.update(kwargs)
    return AccountSummary(**payload)


def _pin_mandate(monkeypatch, path) -> None:
    monkeypatch.setattr("backend.trading.safety.mandate.REPO_MANDATE_FILE", path)
    monkeypatch.setattr("backend.trading.safety.mandate.BACKEND_MANDATE_FILE", path)
    monkeypatch.setattr("backend.trading.safety.mandate.MANDATE_FILE", path)


def _isolate(monkeypatch, tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    journal = tmp_path / "trade_journal.json"
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(
        "backend.trading.safety.mandate.DAILY_ORDERS_FILE",
        tmp_path / "mandate_daily_orders.json",
    )
    monkeypatch.setattr("backend.trading.safety.kill_switch.is_halted", lambda: False)
    return journal


def _worker(mandate=None):
    broker = MagicMock()
    store = InMemoryOrderStore()
    manager = OrderManager(broker=broker, store=store)
    worker = TradingWorker(
        broker=broker,
        store=store,
        manager=manager,
        reconciler=MagicMock(),
        strategy_id="stable",
        ticker_universe=["AAPL"],
        mandate=mandate,
        execution_mode="paper",
    )
    account = _account()
    broker.get_account_summary.return_value = account

    def _submit(order):
        order.status = OrderStatus.SUBMITTED
        return order

    broker.submit_order.side_effect = _submit
    return worker, broker, store, account


def _force_buy(worker) -> None:
    def _buy(ticker, df, fundamental_score, llm_signal, current_positions):
        close = float(df["Close"].iloc[-1])
        return Signal(
            ticker=ticker,
            side="buy",
            strategy_id="stable",
            confidence=0.7,
            reason="would-buy-if-mandate-loaded",
            entry_price=close,
            stop_loss_price=round(close * 0.96, 4),
            take_profit_price=round(close * 1.05, 4),
            avg_win_pct=0.08,
            avg_loss_pct=0.04,
        )

    worker.strategy.generate_signal = _buy
    worker.strategy.diagnose_entry = lambda *a, **k: None


def _run_entries(worker, account, df) -> None:
    with patch.object(worker, "_detect_regime", return_value={"regime": "bull", "target_allocation": 0.90}), \
         patch.object(worker, "_fetch_vix", return_value=None), \
         patch.object(worker, "_fetch_ohlcv", return_value=df), \
         patch.object(
             worker,
             "_get_fundamental_score_info",
             return_value={"score": 80.0, "stale": False, "source": "last_scan"},
         ), \
         patch.object(worker, "_get_top5_tickers", return_value=[]), \
         patch.object(worker, "_get_deep_trade_plan", return_value=None), \
         patch.object(worker, "_safe_get_account", return_value=account):
        worker._run_strategy_signals(account)


def _assert_no_success(broker, store, journal) -> None:
    broker.submit_order.assert_not_called()
    orders = store.get_recent_orders()
    assert orders
    assert all(order.status == OrderStatus.REJECTED for order in orders)
    assert all(order.status not in _SUCCESS for order in orders)
    assert any("fail closed" in (order.error_message or "").lower() for order in orders)
    rows = load_raw_entries(journal)
    assert rows
    assert any(
        "fail closed" in str(row.get("user_visible_summary") or "").lower()
        and row.get("status") == "risk_rejected"
        for row in rows
    )
    assert not any(row.get("status") in ("filled", "submitted") for row in rows)


def test_missing_mandate_buy_does_not_succeed(tmp_path, monkeypatch):
    """Pre-fix: missing mandate.json → permissive → broker submit."""
    journal = _isolate(monkeypatch, tmp_path)
    missing = tmp_path / "no-mandate.json"
    _pin_mandate(monkeypatch, missing)
    worker, broker, store, account = _worker()
    assert worker.mandate_gate.mandate is None
    assert worker.mandate_gate.unavailable_reason
    _force_buy(worker)
    _run_entries(worker, account, _bars())
    _assert_no_success(broker, store, journal)
    actions = {row.get("action") for row in worker.last_signal_summary}
    assert "BUY" not in actions
    assert "BLOCKED" in actions


def test_unreadable_mandate_buy_does_not_succeed(tmp_path, monkeypatch):
    """Pre-fix: corrupt mandate.json → permissive → broker submit."""
    journal = _isolate(monkeypatch, tmp_path)
    bad = tmp_path / "mandate.json"
    bad.write_text("{not-json", encoding="utf-8")
    _pin_mandate(monkeypatch, bad)
    worker, broker, store, account = _worker()
    assert worker.mandate_gate.mandate is None
    _force_buy(worker)
    _run_entries(worker, account, _bars())
    _assert_no_success(broker, store, journal)
    assert any(
        "corrupt" in (order.error_message or "").lower()
        for order in store.get_recent_orders()
    )


def test_missing_mandate_sell_does_not_succeed(tmp_path, monkeypatch):
    """Pre-fix: missing file still allowed the exit because permissive allows sell."""
    journal = _isolate(monkeypatch, tmp_path)
    _pin_mandate(monkeypatch, tmp_path / "absent.json")
    worker, broker, store, _account_obj = _worker()
    held = _account(
        positions=[
            Position(
                id="p1",
                symbol="AAPL",
                quantity=10.0,
                average_entry_price=90.0,
                current_price=100.0,
            )
        ]
    )
    broker.get_account_summary.return_value = held
    result = worker._submit_sell("AAPL", 10.0, 100.0, "stop")
    assert result is not None
    assert result.status == OrderStatus.REJECTED
    assert result.status not in _SUCCESS
    assert "fail closed" in (result.error_message or "").lower()
    broker.submit_order.assert_not_called()
    saved = store.get_order(result.id)
    assert saved is not None
    assert saved.status == OrderStatus.REJECTED
    rows = load_raw_entries(journal)
    assert any(
        "fail closed" in str(row.get("user_visible_summary") or "").lower()
        and row.get("status") == "risk_rejected"
        for row in rows
    )


def test_explicit_empty_mandate_file_still_allows_buy(tmp_path, monkeypatch):
    """``{}`` is the existing no-cap policy. Missing-file fail-closed must not eat it."""
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "mandate.json"
    path.write_text("{}", encoding="utf-8")
    _pin_mandate(monkeypatch, path)
    worker, broker, store, account = _worker()
    assert worker.mandate_gate.unavailable_reason is None
    assert worker.mandate_gate.mandate is not None
    assert worker.mandate_gate.mandate.max_daily_orders is None
    assert worker.mandate_gate.mandate.model_dump() == TradingMandate.permissive().model_dump()
    _force_buy(worker)
    _run_entries(worker, account, _bars())
    broker.submit_order.assert_called_once()
    sent = broker.submit_order.call_args[0][0]
    assert sent.symbol == "AAPL"
    assert sent.status == OrderStatus.SUBMITTED
    assert sent.status != OrderStatus.REJECTED
    assert not any(
        "fail closed" in (order.error_message or "").lower()
        for order in store.get_recent_orders()
    )


def test_explicit_mandate_object_ignores_missing_file(tmp_path, monkeypatch):
    """In-process TradingMandate is an explicit policy, not a missing file."""
    _isolate(monkeypatch, tmp_path)
    _pin_mandate(monkeypatch, tmp_path / "absent.json")
    worker, broker, _store, _account_obj = _worker(
        mandate=TradingMandate(max_notional_per_order=50_000.0)
    )
    assert worker.mandate_gate.mandate is not None
    assert worker.mandate_gate.unavailable_reason is None
    held = _account(
        positions=[
            Position(
                id="p1",
                symbol="AAPL",
                quantity=10.0,
                average_entry_price=90.0,
                current_price=100.0,
            )
        ]
    )
    broker.get_account_summary.return_value = held
    result = worker._submit_sell("AAPL", 10.0, 100.0, "stop")
    assert result.status == OrderStatus.SUBMITTED
    broker.submit_order.assert_called_once()
