"""Operator vs guest on FastAPI Desk — paper APIs fail closed for LAN guests."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.api.desk_auth import GUEST_FORBIDDEN, is_loopback_host, is_operator, token_matches


ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "web" / "static" / "index.html"
PAPER_PATHS = (
    "/api/account",
    "/api/positions",
    "/api/orders",
    "/api/ops",
    "/api/ai-mode",
    "/api/worker/status",
    "/api/digest",
    "/api/performance",
)
PAPER_POSTS = (
    "/api/ops/kill",
    "/api/ops/resume",
    "/api/worker/stop",
)


@pytest.fixture
def desk_token(monkeypatch):
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "unit-test-operator")
    monkeypatch.setenv("APCA_PAPER", "true")
    monkeypatch.setattr("backend.trading.telegram_inbox.start_background", lambda: (lambda: None))
    monkeypatch.setattr("backend.api.scan_scheduler.start_background", lambda: (lambda: None))
    return "unit-test-operator"


@pytest.fixture
def guest_client(desk_token):
    from backend.api.app import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def operator_client(desk_token):
    from backend.api.app import app

    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers.update({"X-Desk-Operator-Token": desk_token})
        yield client


@pytest.fixture
def loopback_client(desk_token):
    from backend.api.app import app

    with TestClient(app, raise_server_exceptions=False, client=("127.0.0.1", 50000)) as client:
        yield client


def test_loopback_hosts():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("localhost")
    assert is_loopback_host("::ffff:127.0.0.1")
    assert not is_loopback_host("192.168.1.20")
    assert not is_loopback_host("testclient")
    assert not is_loopback_host("")


def test_empty_token_never_matches(monkeypatch):
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "")
    assert token_matches("") is False
    assert token_matches("anything") is False


def test_html_exposes_paper_controls_flag():
    html = INDEX.read_text(encoding="utf-8")
    assert "paper_controls" in html
    assert "cgab-paper-controls" in html


def test_guest_meta_hides_paper_controls(guest_client):
    res = guest_client.get("/api/meta")
    assert res.status_code == 200
    body = res.json()
    assert body["paper_controls"] is False
    assert body["role"] == "guest"


def test_guest_health_ok(guest_client):
    res = guest_client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


@pytest.mark.parametrize("path", PAPER_PATHS)
def test_guest_paper_reads_403(guest_client, path):
    res = guest_client.get(path)
    assert res.status_code == 403, path
    assert GUEST_FORBIDDEN in str(res.json().get("detail"))


@pytest.mark.parametrize("path", PAPER_POSTS)
def test_guest_paper_writes_403(guest_client, path):
    res = guest_client.post(path)
    assert res.status_code == 403, path


def test_guest_worker_start_403(guest_client):
    res = guest_client.post("/api/worker/start", json={"strategy": "research_list", "interval": 60})
    assert res.status_code == 403


def test_guest_order_submit_403(guest_client):
    res = guest_client.post("/api/orders", json={"symbol": "AAPL", "side": "buy", "quantity": 1})
    assert res.status_code == 403


def test_guest_ai_mode_write_403(guest_client):
    res = guest_client.put("/api/ai-mode", json={"strategy": "research_list"})
    assert res.status_code == 403


def test_guest_paper_tear_403(guest_client):
    assert guest_client.get("/api/lab/tear").status_code == 403
    assert guest_client.post("/api/lab/tear", json={}).status_code == 403
    assert guest_client.get("/api/lab/tear.html").status_code == 403


def test_guest_ticker_tear_allowed(guest_client):
    res = guest_client.get("/api/lab/tear?ticker=AAPL")
    assert res.status_code == 200
    body = res.json()
    assert body.get("scope") != "paper"
    assert body.get("places_order") is False


def test_operator_token_meta(operator_client):
    res = operator_client.get("/api/meta")
    assert res.status_code == 200
    body = res.json()
    assert body["paper_controls"] is True
    assert body["role"] == "operator"


def test_operator_cookie_allowed(guest_client, desk_token):
    guest_client.cookies.set("DESK_OPERATOR_TOKEN", desk_token)
    res = guest_client.get("/api/ops")
    assert res.status_code == 200


def test_loopback_operator_without_token(loopback_client):
    res = loopback_client.get("/api/meta")
    assert res.status_code == 200
    assert res.json()["paper_controls"] is True
    ops = loopback_client.get("/api/ops")
    assert ops.status_code == 200


def test_operator_worker_status_not_guest_forbidden(operator_client):
    res = operator_client.get("/api/worker/status")
    assert res.status_code != 403


def test_is_operator_uses_header(monkeypatch):
    monkeypatch.setenv("DESK_OPERATOR_TOKEN", "abc")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/meta",
        "raw_path": b"/api/meta",
        "query_string": b"",
        "headers": [(b"x-desk-operator-token", b"abc")],
        "client": ("192.168.1.20", 1234),
        "server": ("0.0.0.0", 8000),
    }
    req = Request(scope, receive)
    assert is_operator(req) is True
