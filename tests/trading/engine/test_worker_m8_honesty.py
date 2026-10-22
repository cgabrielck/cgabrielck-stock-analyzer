"""plan-13b fail-cases: StrategyBase → worker hand-off must not look approved.

Pre-fix on main @ cab4587:

* MandateGate.evaluate ran at quantity=1. A cap above one share and below
  the Kelly/1R order still reached the broker (RiskEngine cap was wider).
* size_shares_by_risk turned stop >= entry into a 5% stop and the worker
  submitted that buy, including after a deep-plan overlay wrote the bad stop.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from backend.trading.engine.order_manager import InMemoryOrderStore, OrderManager
from backend.trading.engine.worker import TradingWorker
from backend.trading.models import AccountSummary, OrderStatus
from backend.trading.risk.gates import RiskLimits
from backend.trading.safety import audit
from backend.trading.safety.mandate import TradingMandate
from backend.trading.strategies.base import Signal
from backend.utils.provenance import journal_store

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


def _account() -> AccountSummary:
    return AccountSummary(
        cash=100_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
        positions=[],
    )


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


def _worker(mandate: TradingMandate):
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
        # Wider than the sized notional so RiskEngine cannot hide a mandate miss.
        risk_limits=RiskLimits(max_order_notional=100_000.0),
    )
    account = _account()
    broker.get_account_summary.return_value = account

    def _submit(order):
        order.status = OrderStatus.SUBMITTED
        store.save_order(order)
        return order

    broker.submit_order.side_effect = _submit
    return worker, broker, store, account


def _force_buy(worker, *, stop_mult: float = 0.96) -> None:
    def _buy(ticker, df, fundamental_score, llm_signal, current_positions):
        close = float(df["Close"].iloc[-1])
        return Signal(
            ticker=ticker,
            side="buy",
            strategy_id="stable",
            confidence=0.7,
            reason="plan-13b harness",
            entry_price=close,
            stop_loss_price=round(close * stop_mult, 4),
            take_profit_price=round(close * 1.08, 4),
            avg_win_pct=0.08,
            avg_loss_pct=0.04,
        )

    worker.strategy.generate_signal = _buy
    worker.strategy.diagnose_entry = lambda *a, **k: None


def _run(worker, account, df, *, plan=None) -> None:
    with patch.object(worker, "_detect_regime", return_value={"regime": "bull", "target_allocation": 0.90}), \
         patch.object(worker, "_fetch_vix", return_value=None), \
         patch.object(worker, "_fetch_ohlcv", return_value=df), \
         patch.object(
             worker,
             "_get_fundamental_score_info",
             return_value={"score": 80.0, "stale": False, "source": "last_scan"},
         ), \
         patch.object(worker, "_get_top5_tickers", return_value=[]), \
         patch.object(worker, "_get_deep_trade_plan", return_value=plan), \
         patch.object(worker, "_safe_get_account", return_value=account):
        worker._run_strategy_signals(account)


def test_sized_order_above_mandate_cap_does_not_submit(tmp_path, monkeypatch):
    """1-share notional ($100) is under the cap; the sized order is not."""
    _isolate(monkeypatch, tmp_path)
    worker, broker, store, account = _worker(
        TradingMandate(max_notional_per_order=150.0)
    )
    _force_buy(worker)
    _run(worker, account, _bars(100.0))

    broker.submit_order.assert_not_called()
    assert store.get_recent_orders() == []
    actions = {row.get("action") for row in worker.last_signal_summary}
    assert "BUY" not in actions
    blocked = [row for row in worker.last_signal_summary if row.get("action") == "BLOCKED"]
    assert blocked
    assert "notional" in str(blocked[-1].get("reason") or "").lower()
    assert all(order.status not in _SUCCESS for order in store.get_recent_orders())


def test_sized_order_under_mandate_cap_still_submits(tmp_path, monkeypatch):
    """Control: a cap above the real notional still reaches the broker."""
    _isolate(monkeypatch, tmp_path)
    worker, broker, store, account = _worker(
        TradingMandate(max_notional_per_order=100_000.0)
    )
    _force_buy(worker)
    _run(worker, account, _bars(100.0))

    broker.submit_order.assert_called_once()
    sent = broker.submit_order.call_args[0][0]
    assert sent.symbol == "AAPL"
    assert sent.quantity > 1
    assert sent.quantity * 100.0 > 150.0
    assert sent.quantity * 100.0 <= 100_000.0
    assert sent.status == OrderStatus.SUBMITTED
    assert sent.status not in {OrderStatus.FILLED, OrderStatus.RISK_APPROVED}


def test_stop_above_entry_does_not_submit(tmp_path, monkeypatch):
    """Deep overlay stop above entry must not be resized as a 5% stop and bought."""
    _isolate(monkeypatch, tmp_path)
    worker, broker, store, account = _worker(TradingMandate.permissive())
    _force_buy(worker, stop_mult=0.96)
    _run(
        worker,
        account,
        _bars(100.0),
        plan={"stop_loss": 150.0, "targets": [180.0], "stance": "avoid", "action": "sell"},
    )

    broker.submit_order.assert_not_called()
    assert store.get_recent_orders() == []
    actions = {row.get("action") for row in worker.last_signal_summary}
    assert "BUY" not in actions
    assert worker.last_skip_counts.get("kelly_zero", 0) >= 1
