"""Fail-cases: worker flatten/exit-while-halted, buys stay blocked.

On current main before this knife, worker comments claimed "exits still
active" under kill, but no test locked buy-block + exit-allow, so copying
Desk D-sell-kill into ``_submit_sell`` would stay green.

Not in scope: Desk flatten-through-kill (stays 409), IntentStore / H1–H3,
D7-DESIGN, fake verified/live, lab advisory → OrderManager.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from backend.trading.engine.desk_orders import (
    KILL_SELL_DETAIL,
    place_desk_order,
    reset_desk_mandate_gate,
)
from backend.trading.engine.order_manager import InMemoryOrderStore, OrderManager
from backend.trading.engine.worker import TradingWorker
from backend.trading.models import (
    AccountSummary,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from backend.trading.risk.gates import RiskEngine, RiskLimits
from backend.trading.safety import audit, kill_switch
from backend.trading.safety.mandate import TradingMandate
from backend.trading.strategies.base import Signal
from backend.utils.provenance import journal_store


ROOT = Path(__file__).resolve().parents[3]
WORKER_PY = ROOT / "backend" / "trading" / "engine" / "worker.py"
KILL_PY = ROOT / "backend" / "trading" / "safety" / "kill_switch.py"
DESK_PY = ROOT / "backend" / "trading" / "engine" / "desk_orders.py"

TIGHT_MANDATE = {
    "allowed_symbols": ["AAPL", "MSFT"],
    "blocked_symbols": ["GME"],
    "max_notional_per_order": 10_000.0,
    "max_daily_orders": 50,
    "allowed_sides": ["buy", "sell"],
}


def _bars(price: float = 100.40, n: int = 40) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [price] * n,
            "High": [price * 1.01] * n,
            "Low": [price * 0.99] * n,
            "Close": [price] * n,
            "Volume": [1_000_000] * n,
        },
        index=pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n),
    )


def _account(**kwargs):
    payload = dict(
        cash=50_000.0,
        buying_power=50_000.0,
        portfolio_value=100_000.0,
        positions=[
            Position(
                id="p1",
                symbol="AAPL",
                quantity=10.0,
                average_entry_price=90.0,
                current_price=100.40,
            )
        ],
    )
    payload.update(kwargs)
    return AccountSummary(**payload)


def _build_worker(mandate=None, account=None, universe=None):
    broker = MagicMock()
    store = InMemoryOrderStore()
    manager = OrderManager(broker=broker, store=store)
    worker = TradingWorker(
        broker=broker,
        store=store,
        manager=manager,
        reconciler=MagicMock(),
        strategy_id="stable",
        ticker_universe=universe or ["AAPL"],
        mandate=mandate or TradingMandate(max_notional_per_order=10_000.0),
        execution_mode="paper",
    )
    worker.risk_engine = RiskEngine(RiskLimits(max_order_notional=10_000.0))
    snapshot = account or _account()
    broker.get_account_summary.return_value = snapshot

    def _submit(order):
        order.status = OrderStatus.SUBMITTED
        order.id = "broker-sell" if order.side == OrderSide.SELL else "broker-buy"
        return order

    broker.submit_order.side_effect = _submit
    return worker, broker, store


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    kill_file = tmp_path / "KILL_SWITCH"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    journal = tmp_path / "trade_journal.json"
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FILE", kill_file)
    monkeypatch.setattr(audit, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(journal_store, "JOURNAL_PATH", journal)
    monkeypatch.setattr(
        "backend.trading.safety.mandate.DAILY_ORDERS_FILE",
        tmp_path / "mandate_daily_orders.json",
    )
    monkeypatch.setattr(
        "backend.trading.risk.shared_state.COOLDOWN_FILE",
        tmp_path / "risk_cooldown.json",
    )
    monkeypatch.setattr(
        "backend.trading.risk.shared_state.DAY_START_FILE",
        tmp_path / "risk_day_start.json",
    )
    reset_desk_mandate_gate()
    yield {
        "kill_file": kill_file,
        "audit_dir": audit_dir,
        "journal": journal,
    }
    reset_desk_mandate_gate()


def _function_def(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.name} is missing function {name}")


def _call_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
            if isinstance(func.value, ast.Name):
                names.add(f"{func.value.id}.{func.attr}")
    return names


def _first_call_lineno(node: ast.AST, *needles: str) -> int | None:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        names = []
        if isinstance(func, ast.Name):
            names.append(func.id)
        elif isinstance(func, ast.Attribute):
            names.append(func.attr)
            if isinstance(func.value, ast.Name):
                names.append(f"{func.value.id}.{func.attr}")
        if any(n in names for n in needles):
            return child.lineno
    return None


def test_source_submit_sell_must_consult_worker_kill_helper_then_mandate_risk_om():
    """Fail-case: deleting the helper or skipping Mandate/Risk/OM turns this red."""
    submit = _function_def(WORKER_PY, "_submit_sell")
    called = _call_names(submit)
    assert "worker_kill_blocks_side" in called
    assert "evaluate" in called
    assert "evaluate_order" in called
    assert "submit_new_order" in called
    assert "stamp_risk_approved" in called
    helper_ln = _first_call_lineno(submit, "worker_kill_blocks_side")
    mandate_ln = _first_call_lineno(submit, "evaluate")
    risk_ln = _first_call_lineno(submit, "evaluate_order")
    om_ln = _first_call_lineno(submit, "submit_new_order")
    assert helper_ln is not None and mandate_ln is not None
    assert risk_ln is not None and om_ln is not None
    assert helper_ln < mandate_ln < risk_ln < om_ln


def test_source_exits_run_before_buy_kill_gate():
    """Fail-case: moving kill to an early return that skips _submit_sell turns this red."""
    run = _function_def(WORKER_PY, "_run_strategy_signals")
    called = _call_names(run)
    assert "_submit_sell" in called
    assert "worker_kill_blocks_side" in called
    exit_ln = _first_call_lineno(run, "_submit_sell")
    buy_ln = _first_call_lineno(run, "worker_kill_blocks_side")
    assert exit_ln is not None and buy_ln is not None
    assert exit_ln < buy_ln


def test_source_desk_must_not_call_worker_flatten_helper():
    """Fail-case: wiring Desk sell through worker_kill_blocks_side reopens D-sell-kill."""
    src = DESK_PY.read_text(encoding="utf-8")
    place = _function_def(DESK_PY, "place_desk_order")
    called = _call_names(place)
    assert "worker_kill_blocks_side" not in called
    assert "is_halted" in called or "kill_switch.is_halted" in called
    body = ast.get_source_segment(src, place) or ast.unparse(place)
    assert "KILL_SELL_DETAIL" in body
    assert "worker_kill_blocks_side(" not in body


def test_source_helper_lives_on_kill_switch_module():
    helper = _function_def(KILL_PY, "worker_kill_blocks_side")
    body = ast.unparse(helper)
    assert "is_halted" in body
    assert '== "buy"' in body or "== 'buy'" in body


def test_kill_on_worker_submit_sell_hits_broker_via_mandate_risk():
    """Kill ON must not hollow-out flatten: Mandate + Risk still run, then OM."""
    kill_switch.engage("flatten-through-kill")
    worker, broker, store = _build_worker()
    mandate_calls = []
    risk_calls = []
    orig_man = worker.mandate_gate.evaluate
    orig_risk = worker.risk_engine.evaluate_order

    def spy_man(order, ref=None):
        mandate_calls.append((order.side, ref))
        return orig_man(order, ref)

    def spy_risk(order, account, **kwargs):
        risk_calls.append(order.side)
        return orig_risk(order, account, **kwargs)

    worker.mandate_gate.evaluate = spy_man
    worker.risk_engine.evaluate_order = spy_risk

    result = worker._submit_sell("AAPL", 10.0, 100.40, "stop")
    assert result is not None
    assert result.status == OrderStatus.SUBMITTED
    assert result.status != OrderStatus.REJECTED
    broker.submit_order.assert_called_once()
    sent = broker.submit_order.call_args[0][0]
    assert sent.side == OrderSide.SELL
    assert sent.limit_price == 100.40
    assert mandate_calls == [(OrderSide.SELL, 100.40)]
    assert risk_calls == [OrderSide.SELL]
    saved = store.get_order(result.id)
    assert saved is not None
    assert saved.status == OrderStatus.SUBMITTED


def test_kill_on_worker_tick_exit_hits_broker(monkeypatch):
    """Hollow 'exits still active' without _submit_sell reaching the broker is a bug."""
    kill_switch.engage("flatten-through-kill")
    worker, broker, _store = _build_worker()

    class _Exit:
        reason = "stop"
        exit_price = 100.40

    monkeypatch.setattr(worker.strategy, "check_exit", lambda **kwargs: _Exit())
    monkeypatch.setattr(worker.strategy, "generate_signal", lambda **kwargs: None)
    monkeypatch.setattr(worker.strategy, "diagnose_entry", lambda *a, **k: None)
    account = _account()
    worker._safe_get_account = lambda: account
    worker._detect_regime = lambda: {"regime": "neutral", "target_allocation": 0.70}
    worker._fetch_vix = lambda: None
    df = _bars()
    worker._fetch_ohlcv = lambda *a, **k: df
    worker._get_fundamental_score_info = lambda t: {
        "score": 80.0, "source": "cache", "stale": False,
    }
    worker._run_strategy_signals(account)
    actions = {row.get("action") for row in worker.last_signal_summary}
    assert any(str(a).startswith("EXIT:") for a in actions)
    assert "EXIT_BLOCKED" not in actions
    broker.submit_order.assert_called_once()
    sent = broker.submit_order.call_args[0][0]
    assert sent.side == OrderSide.SELL
    assert sent.symbol == "AAPL"


def test_kill_on_worker_tick_buy_does_not_hit_broker(monkeypatch):
    """Fail-case: removing the buy kill continue would submit this order."""
    kill_switch.engage("flatten-through-kill")
    worker, broker, store = _build_worker(
        account=_account(positions=[]),
        universe=["AAPL"],
    )
    account = _account(positions=[])
    worker._safe_get_account = lambda: account
    broker.get_account_summary.return_value = account

    def _buy(ticker, df, fundamental_score, llm_signal, current_positions):
        close = float(df["Close"].iloc[-1])
        return Signal(
            ticker=ticker,
            side="buy",
            strategy_id="stable",
            confidence=0.7,
            reason="exit-while-halted harness",
            entry_price=close,
            stop_loss_price=round(close * 0.96, 4),
            take_profit_price=round(close * 1.08, 4),
            avg_win_pct=0.08,
            avg_loss_pct=0.04,
        )

    monkeypatch.setattr(worker.strategy, "generate_signal", _buy)
    monkeypatch.setattr(worker.strategy, "diagnose_entry", lambda *a, **k: None)
    monkeypatch.setattr(worker.strategy, "check_exit", lambda **kwargs: None)
    worker._detect_regime = lambda: {"regime": "bull", "target_allocation": 0.90}
    worker._fetch_vix = lambda: None
    worker._fetch_ohlcv = lambda *a, **k: _bars(100.0)
    worker._get_fundamental_score_info = lambda t: {
        "score": 80.0, "source": "last_scan", "stale": False,
    }
    worker._get_top5_tickers = lambda: []
    worker._get_deep_trade_plan = lambda t: None
    worker._run_strategy_signals(account)

    broker.submit_order.assert_not_called()
    assert store.get_recent_orders() == []
    actions = {row.get("action") for row in worker.last_signal_summary}
    assert "BUY" not in actions
    blocked = [row for row in worker.last_signal_summary if row.get("action") == "BLOCKED"]
    assert blocked
    assert blocked[-1].get("reason") == "kill_switch"
    assert worker.last_skip_counts.get("kill_switch", 0) >= 1


def test_kill_on_does_not_let_worker_sell_bypass_mandate():
    kill_switch.engage("flatten-through-kill")
    worker, broker, _store = _build_worker(
        mandate=TradingMandate(allowed_sides=["buy"]),
    )
    result = worker._submit_sell("AAPL", 10.0, 100.40, "stop")
    assert result.status == OrderStatus.REJECTED
    assert result.status != OrderStatus.SUBMITTED
    assert "sell" in (result.error_message or "").lower()
    broker.submit_order.assert_not_called()


def test_kill_on_does_not_let_worker_sell_bypass_risk_notional():
    """qty=100 × $100.40 = $10,040 still exceeds $10k even under kill."""
    kill_switch.engage("flatten-through-kill")
    worker, broker, _store = _build_worker()
    result = worker._submit_sell("AAPL", 100.0, 100.40, "stop")
    assert result.status == OrderStatus.REJECTED
    assert "exceeds limit" in (result.error_message or "").lower()
    broker.submit_order.assert_not_called()


def test_inverting_helper_to_block_sell_is_observed_by_submit_sell(monkeypatch):
    """Fail-case: helper must be live — a Desk-style sell block must reject."""
    kill_switch.engage("flatten-through-kill")
    monkeypatch.setattr(kill_switch, "worker_kill_blocks_side", lambda side: True)
    worker, broker, _store = _build_worker()
    result = worker._submit_sell("AAPL", 10.0, 100.40, "stop")
    assert result.status == OrderStatus.REJECTED
    assert "worker exits blocked" in (result.error_message or "").lower()
    broker.submit_order.assert_not_called()


def test_desk_sell_still_409_under_kill(tmp_path):
    """Regression lock from #19: do not invent Desk flatten-through-kill."""
    path = tmp_path / "mandate.json"
    path.write_text(json.dumps(TIGHT_MANDATE), encoding="utf-8")
    kill_switch.engage("d-sell-kill regression")
    broker = MagicMock()
    broker.get_account_summary.return_value = _account(positions=[])
    result = place_desk_order(
        Order(
            id="desk-sell-1",
            symbol="AAPL",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=1,
            limit_price=150.0,
            idempotency_key="desk-sell-1",
        ),
        broker=broker,
        store=InMemoryOrderStore(),
        mandate_path=path,
        quote_fn=lambda _s: 150.0,
    )
    assert result.blocked is True
    assert result.http_status == 409
    assert result.http_status != 200
    assert result.detail == KILL_SELL_DETAIL
    assert result.gate == "kill"
    broker.get_account_summary.assert_not_called()
    broker.submit_order.assert_not_called()


def test_kill_on_sell_audit_records_flatten_allowed(_isolate):
    kill_switch.engage("flatten-through-kill")
    worker, broker, _store = _build_worker()
    worker._submit_sell("AAPL", 10.0, 100.40, "stop")
    broker.submit_order.assert_called_once()
    events = audit.read_recent_events(audit_dir=_isolate["audit_dir"], limit=50)
    kill_rows = [
        e for e in events
        if e.get("event_type") == "kill_switch_check" and e.get("side") == "sell"
    ]
    assert kill_rows
    assert kill_rows[-1].get("worker_flatten_allowed") is True
    assert kill_rows[-1].get("halted") is True
    journal = journal_store.load_raw_entries()
    assert journal
    last = journal[-1]
    assert last.get("verified") is not True
    assert last.get("live") is not True
    prov = last.get("provenance") or {}
    assert prov.get("verified") is not True
    assert prov.get("live") is not True
