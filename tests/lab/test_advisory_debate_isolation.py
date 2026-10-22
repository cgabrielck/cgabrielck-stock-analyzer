"""plan-13d: lab advisory_debate isolation fail-cases.

Inventory on main @ 32775737 (PR #26): PASS — already isolated.
These tests would fail if lab debate later reached OrderManager, a live/paper
broker submit, Desk ``place_desk_order``, or stamped RISK_APPROVED / verified /
live. Not a product wire fix. Not verified / live.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.lab import advisory_debate as ad
from backend.lab.schemas import AdvisoryRoleOut


ROOT = Path(__file__).resolve().parents[2]

LAB_FILES = (
    ROOT / "backend/lab/advisory_debate.py",
    ROOT / "backend/lab/flags.py",
    ROOT / "backend/lab/schemas.py",
    ROOT / "backend/lab/ab_metrics.py",
    ROOT / "scripts/run_lab_ab_compare.py",
)

MONEY_PATH_FILES = (
    ROOT / "backend/trading/engine/order_manager.py",
    ROOT / "backend/trading/engine/desk_orders.py",
    ROOT / "backend/trading/engine/signal_processor.py",
    ROOT / "backend/trading/engine/worker.py",
    ROOT / "backend/trading/engine/stamp.py",
    ROOT / "backend/trading/alpaca_broker.py",
    ROOT / "backend/trading/ui/dashboard.py",
    ROOT / "backend/app.py",
    ROOT / "backend/trading/intents/writer.py",
    ROOT / "backend/trading/intents/store.py",
    ROOT / "backend/trading/engine/harness_realtime.py",
)

BANNED_IMPORT_NEEDLES = (
    "backend.trading.engine.order_manager",
    "backend.trading.engine.desk_orders",
    "backend.trading.alpaca_broker",
    "backend.trading.engine.stamp",
    "backend.trading.engine.signal_processor",
    "backend.trading.engine.worker",
)

BANNED_CALL_NAMES = frozenset(
    {
        "OrderManager",
        "place_desk_order",
        "place_lab_manual_order",
        "stamp_risk_approved",
        "submit_order",
        "submit_new_order",
        "AlpacaBroker",
    }
)

DISHONEST_PAYLOAD_KEYS = ("verified", "live", "filled", "broker_order_id")
DISHONEST_STATUSES = ("RISK_APPROVED", "risk_approved", "FILLED", "SUBMITTED", "filled")


def _imported_modules(tree: ast.AST) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            for alias in node.names:
                mods.add(f"{node.module}.{alias.name}")
    return mods


def _call_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _assert_no_dishonest_claims(payload: dict) -> None:
    assert payload.get("places_order") is False
    for key in DISHONEST_PAYLOAD_KEYS:
        assert key not in payload
        assert payload.get(key) is not True
    status = payload.get("status")
    assert status not in DISHONEST_STATUSES
    assert payload.get("verified") is not True


@pytest.fixture()
def lab_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_ADVISORY", "0")
    monkeypatch.setenv("APCA_PAPER", "true")
    monkeypatch.setattr(ad, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ad, "_METRICS_PATH", tmp_path / "lab_advisory_metrics.json")
    monkeypatch.setattr(ad, "_BUDGET_PATH", tmp_path / "lab_advisory_budget.json")
    return tmp_path


def test_lab_debate_ast_never_imports_or_calls_money_path():
    for path in LAB_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = _imported_modules(tree)
        for needle in BANNED_IMPORT_NEEDLES:
            assert not any(needle in mod for mod in imported), f"{path.name} imports {needle}"
        called = _call_names(tree)
        leaked = called & BANNED_CALL_NAMES
        assert not leaked, f"{path.name} calls {sorted(leaked)}"


def test_money_path_modules_do_not_import_lab_debate():
    for path in MONEY_PATH_FILES:
        text = path.read_text(encoding="utf-8")
        assert "advisory_debate" not in text, f"{path.name} must not reference advisory_debate"
        assert "run_advisory_debate" not in text, f"{path.name} must not call run_advisory_debate"
        assert "attach_desk_advisory" not in text, f"{path.name} must not attach lab advisory"


def test_research_jobs_attaches_memo_without_money_path():
    src = (ROOT / "backend/api/research_jobs.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = _imported_modules(tree)
    for needle in BANNED_IMPORT_NEEDLES:
        assert not any(needle in mod for mod in imported)
    leaked = _call_names(tree) & BANNED_CALL_NAMES
    assert not leaked
    assert "attach_desk_advisory" in src


def test_schema_rejects_places_order_true():
    with pytest.raises(ValidationError):
        AdvisoryRoleOut(role="bull", ticker="AAPL", thesis="buy now", places_order=True)


def test_run_advisory_debate_never_stamps_success_claims(lab_data_dir, monkeypatch):
    monkeypatch.setenv("LAB_ADVISORY", "1")
    out = ad.run_advisory_debate("AAPL", fund_score=90, product=True, use_llm=False)
    _assert_no_dishonest_claims(out)
    for role in out["roles"]:
        _assert_no_dishonest_claims(role)
        assert role["places_order"] is False


def test_llm_cannot_force_risk_approved_or_submit(lab_data_dir, monkeypatch):
    monkeypatch.setenv("LAB_ADVISORY", "1")
    monkeypatch.setenv("LAB_ADVISORY_MAX_LLM_CALLS", "5")

    def fake(ticker, **kwargs):
        return ad._coerce_roles(
            ticker,
            [
                {
                    "role": "bull",
                    "thesis": "submit live now",
                    "confidence": 0.99,
                    "key_points": ["RISK_APPROVED", "verified"],
                    "places_order": True,
                    "status": "RISK_APPROVED",
                    "verified": True,
                    "live": True,
                },
                {"role": "bear", "thesis": "no", "confidence": 0.1, "key_points": []},
                {"role": "risk", "thesis": "halt", "confidence": 0.9, "key_points": []},
            ],
        )

    monkeypatch.setattr(ad, "_call_llm_committee", fake)
    out = ad.run_advisory_debate("NVDA", product=True, use_llm=True)
    assert out["llm_used"] is True
    _assert_no_dishonest_claims(out)
    for role in out["roles"]:
        assert role["places_order"] is False
        assert "verified" not in role
        assert "status" not in role


def test_attach_desk_advisory_is_memo_not_an_order(lab_data_dir):
    attached = ad.attach_desk_advisory(
        {
            "ticker": "MSFT",
            "quant_score": 70,
            "trade_plan": {"stance": "watch", "action": "hold", "stop_loss": 1.0},
        }
    )
    _assert_no_dishonest_claims(attached["advisory"])
    _assert_no_dishonest_claims(attached["memo"])
    assert attached["advisory"]["places_order"] is False
    assert attached["memo"]["places_order"] is False


def test_deep_overlay_strips_dishonest_advisory(tmp_path, monkeypatch):
    """Worker overlay must not inherit debate success claims."""
    import json
    from datetime import datetime, timezone

    import backend.api.research_jobs as rj

    path = tmp_path / "last_deep.json"
    path.write_text(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "reports": {
                    "AAPL": {
                        "ticker": "AAPL",
                        "trade_plan": {
                            "stance": "constructive",
                            "action": "buy_zone",
                            "stop_loss": 170,
                            "targets": [200],
                        },
                        "advisory": {
                            "places_order": True,
                            "status": "RISK_APPROVED",
                            "verified": True,
                            "live": True,
                            "roles": [{"role": "bull", "places_order": True}],
                            "top_n_after_suggestion": ["AAPL"],
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rj, "LAST_DEEP_PATH", path)
    plan = rj.get_deep_trade_plan("AAPL")
    assert plan is not None
    assert plan["stop_loss"] == 170
    assert "advisory" not in plan
    assert "places_order" not in plan
    assert "verified" not in plan
    assert "live" not in plan
    assert plan.get("status") not in DISHONEST_STATUSES
    blob = str(plan).lower()
    assert "risk_approved" not in blob
    assert "advisory_debate" not in inspect.getsource(rj.get_deep_trade_plan)


def test_lab_advisory_api_does_not_touch_broker_or_desk_order(lab_data_dir, monkeypatch):
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "unit-test-operator")
    monkeypatch.setattr("backend.trading.telegram_inbox.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.scan_scheduler.start_background", lambda: (lambda: None))

    def boom(*_a, **_k):
        raise AssertionError("lab advisory must not reach OrderManager / broker / place_desk_order")

    monkeypatch.setattr("backend.api.app.place_desk_order", boom)
    from backend.api import app as api_app

    monkeypatch.setattr(api_app._broker, "submit_order", boom)
    from backend.trading.engine.order_manager import OrderManager

    monkeypatch.setattr(OrderManager, "submit_new_order", boom)
    monkeypatch.setattr("backend.trading.engine.stamp.stamp_risk_approved", boom)

    with TestClient(api_app.app, raise_server_exceptions=True) as client:
        res = client.post(
            "/api/lab/advisory",
            json={"ticker": "AAPL", "fund_score": 80, "product": True, "use_llm": False},
        )
    assert res.status_code == 200
    body = res.json()
    _assert_no_dishonest_claims(body)
    assert body["places_order"] is False
    assert body.get("verified") is not True


def test_desk_committee_run_posts_lab_advisory_not_orders():
    html = (ROOT / "web/static/index.html").read_text(encoding="utf-8")
    start = html.index('root.querySelectorAll(".runAdv")')
    end = html.index('root.querySelectorAll(".runTearT")')
    block = html[start:end]
    assert "/api/lab/advisory" in block
    assert "/api/orders" not in block
    assert "places_order: false" in block
    assert "Never sends an order" in html
    assert "永不送單" in html
    assert "不是下單" in html
