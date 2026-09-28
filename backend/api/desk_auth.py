"""Desk operator vs research-guest access.

Fail-closed: only loopback (127.0.0.1 / ::1) or a matching DESK_OPERATOR_TOKEN
is the operator. LAN IPs without the token are guests. Do not trust
X-Forwarded-For. An empty token never authenticates.
"""
from __future__ import annotations

import os
import secrets
from ipaddress import ip_address
from typing import Optional

from fastapi import HTTPException, Request

OPERATOR_HEADER = "x-desk-operator-token"
OPERATOR_COOKIE = "DESK_OPERATOR_TOKEN"
GUEST_FORBIDDEN = "Paper controls are limited to the local operator."


def operator_token() -> str:
    return (os.getenv("DESK_OPERATOR_TOKEN") or "").strip()


def is_loopback_host(host: Optional[str]) -> bool:
    if not host:
        return False
    h = host.strip().lower().strip("[]")
    if h in {"localhost", "::1"}:
        return True
    if h.startswith("::ffff:"):
        h = h[7:]
    try:
        return bool(ip_address(h).is_loopback)
    except ValueError:
        return False


def provided_token(request: Request) -> str:
    header = (request.headers.get(OPERATOR_HEADER) or "").strip()
    if header:
        return header
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.cookies.get(OPERATOR_COOKIE) or "").strip()


def token_matches(provided: str) -> bool:
    expected = operator_token()
    if not expected or not provided:
        return False
    if len(expected) != len(provided):
        secrets.compare_digest(expected, expected)
        return False
    return secrets.compare_digest(expected, provided)


def is_operator(request: Request) -> bool:
    client = request.client.host if request.client else None
    if is_loopback_host(client):
        return True
    return token_matches(provided_token(request))


def require_operator(request: Request) -> None:
    if not is_operator(request):
        raise HTTPException(status_code=403, detail=GUEST_FORBIDDEN)
