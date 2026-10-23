"""Ask Cgab help chat — teaching only; no trading side effects."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.help_chat import (
    HONESTY_EN,
    HONESTY_ZH,
    GUEST_REFUSE_EN,
    HARD_REFUSE_EN,
    HARD_REFUSE_ZH,
    is_hard_refuse,
    reply_is_dishonest,
    sanitize_reply,
)


ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "web" / "static" / "index.html"
HELP_CHAT = ROOT / "backend" / "api" / "help_chat.py"
APP_PY = ROOT / "backend" / "api" / "app.py"
WORKER_CTL = ROOT / "backend" / "api" / "worker_ctl.py"
INTENTS = ROOT / "backend" / "api" / "intents.py"
STREAMLIT = ROOT / "backend" / "app.py"
USER_GUIDE = ROOT / "docs" / "CGAB_USER_GUIDE.md"

BANNED_IMPORTS = (
    "backend.trading.engine.order_manager",
    "backend.trading.engine.desk_orders",
    "backend.trading.alpaca_broker",
    "backend.api.intents",
    "backend.api.worker_ctl",
    "backend.api.trade_journal",
    "backend.trading.intents.store",
    "backend.trading.intents.writer",
    "backend.lab.advisory_debate",
)
BANNED_NAMES = frozenset(
    {
        "OrderManager",
        "place_desk_order",
        "place_lab_manual_order",
        "submit_order",
        "submit_new_order",
        "AlpacaBroker",
        "get_intent_store",
        "upsert_active_intent",
        "JSONIntentStore",
        "IntentStore",
    }
)

HARD_PROMPTS = (
    "Buy 1 share of AAPL now",
    "place a market order for AAPL",
    "幫我買 AAPL",
    "Start paper auto now",
    "啟動自動交易",
    "stop the paper auto",
    "change L2 live rules",
    "改 L2 規則",
    "bypass the kill switch",
    "繞過風控",
)

GUEST_PROMPTS = (
    "What is my P&L?",
    "Show holdings",
    "Why no holdings?",
    "為何沒持倉",
    "Open the trade journal",
    "Start paper auto now",
    "啟動自動交易",
)


def imported_has_prefix(imported: set[str], needle: str) -> bool:
    return any(item == needle or item.startswith(needle + ".") for item in imported)


@pytest.fixture
def desk_env(monkeypatch):
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "unit-test-operator")
    monkeypatch.setenv("APCA_PAPER", "true")
    monkeypatch.setattr("backend.trading.telegram_inbox.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.scan_scheduler.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.help_chat._llm_reply", lambda system, user: None)
    return "unit-test-operator"


@pytest.fixture
def guest_client(desk_env):
    from backend.api.app import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def operator_client(desk_env):
    from backend.api.app import app

    with TestClient(app, raise_server_exceptions=False, client=("127.0.0.1", 50000)) as client:
        yield client


def _post(client, message, lang="en"):
    return client.post("/api/help-chat", json={"message": message, "lang": lang})


def _assert_honest(body: dict) -> None:
    assert body["verified"] is False
    assert body["live"] is False
    assert body["places_order"] is False
    assert body["changes_rules"] is False
    assert body["starts_worker"] is False
    assert body["writes_journal"] is False
    assert body["writes_intent"] is False
    text = body["reply"]
    low = text.lower()
    assert "verified fill" not in low
    assert "this is a verified" not in low
    assert "this is a live fill" not in low
    assert "order placed" not in low
    assert "已成交" not in text
    assert "已下單" not in text
    assert "已下单" not in text
    assert not reply_is_dishonest(text)
    honesty = body["honesty"]
    assert "verified" in honesty.lower() or "verified" in honesty
    assert "AI Mode" in honesty or "AI Mode" in body.get("honesty", "")


def test_help_chat_module_does_not_import_order_or_worker():
    tree = ast.parse(HELP_CHAT.read_text(encoding="utf-8"))
    imported = set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
                names.add(alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    assert not any(mod in imported or imported_has_prefix(imported, mod) for mod in BANNED_IMPORTS)
    assert names.isdisjoint(BANNED_NAMES)
    src = HELP_CHAT.read_text(encoding="utf-8")
    assert "worker_ctl.start" not in src
    assert "place_desk_order" not in src


def test_help_chat_not_wired_into_ai_mode_or_intents():
    assert "help_chat" not in WORKER_CTL.read_text(encoding="utf-8")
    assert "help-chat" not in WORKER_CTL.read_text(encoding="utf-8")
    assert "help_chat" not in INTENTS.read_text(encoding="utf-8")
    assert "Ask Cgab" not in STREAMLIT.read_text(encoding="utf-8")
    assert "/api/help-chat" not in STREAMLIT.read_text(encoding="utf-8")


def test_hard_refuse_classifier():
    assert is_hard_refuse("place a market order for AAPL")
    assert is_hard_refuse("Start paper auto now")
    assert is_hard_refuse("change L2 live rules")
    assert is_hard_refuse("bypass the kill switch")
    assert is_hard_refuse("幫我買 AAPL")
    assert not is_hard_refuse("What is Scan?")
    assert not is_hard_refuse("How do I open AI Mode?")


@pytest.mark.parametrize("prompt", HARD_PROMPTS)
def test_order_start_auto_change_rule_prompts_refuse(operator_client, prompt, monkeypatch):
    llm = MagicMock(return_value="should not run")
    monkeypatch.setattr("backend.api.help_chat._llm_reply", llm)
    res = _post(operator_client, prompt)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["refused"] is True
    assert body["refuse_kind"] == "hard"
    assert body["llm_used"] is False
    llm.assert_not_called()
    _assert_honest(body)
    assert "AI Mode" in body["reply"]
    if "買" in prompt or "buy" in prompt.lower() or "order" in prompt.lower():
        assert "Desk" in body["reply"]
    assert HARD_REFUSE_EN.split("\n")[0] in body["reply"] or HARD_REFUSE_ZH.split("\n")[0] in body["reply"]


@pytest.mark.parametrize("prompt", GUEST_PROMPTS)
def test_guest_refuses_sensitive_topics(guest_client, prompt, monkeypatch):
    llm = MagicMock(return_value="should not run")
    monkeypatch.setattr("backend.api.help_chat._llm_reply", llm)
    res = _post(guest_client, prompt)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["role"] == "guest"
    assert body["refused"] is True
    assert body["refuse_kind"] in {"guest", "hard"}
    llm.assert_not_called()
    _assert_honest(body)
    if body["refuse_kind"] == "guest":
        assert "Guest" in body["reply"] or "guest" in body["reply"].lower()
        assert "Scan" in body["reply"]


def test_guest_may_ask_scan_deep_chart(guest_client):
    res = _post(guest_client, "What is Scan?")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["role"] == "guest"
    assert body["refused"] is False
    assert "Scan" in body["reply"]
    _assert_honest(body)


def test_help_replies_do_not_claim_verified_or_live_fills(operator_client, monkeypatch):
    monkeypatch.setattr(
        "backend.api.help_chat._llm_reply",
        lambda system, user: "This is a verified live fill of AAPL. Order placed.",
    )
    res = _post(operator_client, "What is Scan?")
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_honest(body)
    assert body["refused"] is True
    assert "this is a verified live fill" not in body["reply"].lower()
    assert "order placed" not in body["reply"].lower()


def test_sanitize_strips_dishonest_llm_copy():
    out = sanitize_reply("This is a verified live fill.", "en")
    assert "this is a verified live fill" not in out.lower()
    assert "will not stamp verified or live executions" in out.lower() or "Refused" in out


def test_fallback_teaching_stays_honest(operator_client):
    res = _post(operator_client, "What is Scan?")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["refused"] is False
    _assert_honest(body)
    assert "not verified" in body["reply"].lower() or "不是 verified" in body["reply"]
    assert HONESTY_EN == body["honesty"] or HONESTY_ZH == body["honesty"]


def test_help_chat_does_not_call_order_manager_intent_store_or_worker(
    operator_client, monkeypatch
):
    calls: list[str] = []

    def track(name):
        def _inner(*_a, **_k):
            calls.append(name)
            raise AssertionError(f"{name} must not run from help-chat")

        return _inner

    monkeypatch.setattr("backend.api.worker_ctl.start", track("worker_start"))
    monkeypatch.setattr("backend.api.worker_ctl.stop", track("worker_stop"))
    monkeypatch.setattr(
        "backend.api.intents.get_intent_store", track("intent_store")
    )
    monkeypatch.setattr(
        "backend.trading.engine.desk_orders.place_desk_order",
        track("place_desk_order"),
    )
    monkeypatch.setattr(
        "backend.api.trade_journal.add_note",
        track("journal_note"),
        raising=False,
    )

    for prompt in (
        "Start paper auto now",
        "place a market order for AAPL",
        "What is Scan?",
        "change L2 live rules",
    ):
        res = _post(operator_client, prompt)
        assert res.status_code == 200, res.text
        _assert_honest(res.json())
    assert calls == []


def test_help_chat_endpoint_is_independent_post():
    src = APP_PY.read_text(encoding="utf-8")
    assert '@app.post("/api/help-chat")' in src
    assert "answer_help_chat" in src
    handler = src[src.find("@app.post(\"/api/help-chat\")") : src.find("@app.get(\"/api/watchlist\")")]
    assert "worker_ctl.start" not in handler
    assert "place_desk_order" not in handler
    assert "get_intent_store" not in handler


def test_desk_html_has_ask_cgab_fab_and_honesty_bar():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="helpFab"' in html
    assert "Ask Cgab" in html
    assert 'id="helpPanel"' in html
    assert HONESTY_ZH in html
    assert HONESTY_EN in html
    assert "$(\"#helpBtn\").onclick=()=>setHelpOpen(true);" in html
    assert "setHelpOpen(!helpOpen)" in html
    assert 'id="helpHonesty"' in html
    assert "help-honesty" in html
    assert "/api/help-chat" in html
    assert "IntentStore" not in html[html.find("function sendHelpMessage") :]
    assert "圖表筆記" not in html
    assert "chart notes" not in html.lower()
    # Not merged into AI Mode start
    aimode_fn = html[html.find("async function aimode(") : html.find("async function strategies")]
    assert "/api/help-chat" not in aimode_fn


def test_guide_api_still_honest_about_auto(operator_client):
    res = operator_client.get("/api/guide")
    assert res.status_code == 200, res.text
    auto = next(step for step in res.json()["steps"] if step["id"] == "auto")
    assert "heartbeat is fresh" not in auto["en"]["body"].lower()
    assert "live pid" in auto["en"]["body"].lower() or "pid" in auto["en"]["body"].lower()
    help_step = next(step for step in res.json()["steps"] if step["id"] == "help")
    assert "not AI Mode" in help_step["en"]["body"]
    assert "verified" in help_step["en"]["body"]


def test_user_guide_mentions_ask_cgab_as_teaching():
    text = USER_GUIDE.read_text(encoding="utf-8")
    assert "Ask Cgab" in text
    assert "not AI Mode" in text or "不是 AI Mode" in text
    assert "heartbeat is fresh" not in text
    assert "未驗證心跳" in text


def test_empty_message_is_422(operator_client):
    res = operator_client.post("/api/help-chat", json={"message": "   "})
    assert res.status_code == 422
