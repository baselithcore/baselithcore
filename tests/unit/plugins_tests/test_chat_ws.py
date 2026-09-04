"""Tests for the WebSocket chat endpoint (/chat/ws).

SSE covers one-shot streaming; the WS endpoint adds a persistent
conversational channel. The handshake runs the *same* gate as the REST chat
surface (``require_user``: same credentials, same allowed roles, same
per-identity rate limit), and that gate runs again on every turn so a
long-lived connection is metered like a sequence of REST requests. A rejected
handshake is closed with 4401/4403/4429 before any model spend.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import plugins.api_routers.chat_ws as chat_ws_module
from core.auth.types import AuthRole, AuthUser
from core.config import SecurityConfig
from core.middleware.security import SecurityManager
from plugins.api_routers.chat_ws import router


class _FakeChatService:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.requests: list[Any] = []

    async def handle_chat_stream_async(self, req: Any):
        self.requests.append(req)

        async def _stream():
            for chunk in self._chunks:
                yield chunk

        return _stream()


class _FakeAuthManager:
    def __init__(self, authenticated: bool, role: AuthRole = AuthRole.USER) -> None:
        self._authenticated = authenticated
        self._role = role
        self.headers_seen: list[str | None] = []

    async def authenticate(self, auth_header: str | None) -> AuthUser:
        self.headers_seen.append(auth_header)
        if self._authenticated and auth_header:
            return AuthUser(user_id="user-1", roles={self._role})
        return AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})


def _security_config() -> MagicMock:
    config = MagicMock(spec=SecurityConfig)
    config.auth_required = True
    config.api_keys_admin = set()
    config.api_keys_job = set()
    config.api_keys_user = set()
    config.rate_limit_window_seconds = 60
    config.rate_limit_user_per_minute = 10
    config.auth_failure_limit_per_minute = 20
    return config


def _client(
    monkeypatch,
    *,
    authenticated: bool,
    chunks: list[str] | None = None,
    role: AuthRole = AuthRole.USER,
):
    service = _FakeChatService(chunks or ["Hello ", "world"])
    auth = _FakeAuthManager(authenticated, role=role)
    # The real gate (SecurityManager.enforce_auth) on a fake credential
    # backend and a recording rate limiter: role policy and metering are
    # exercised for real, only credentials and Redis are stubbed.
    manager = SecurityManager(_security_config())
    manager.rate_limiter = AsyncMock()
    monkeypatch.setattr("core.auth.manager.get_auth_manager", lambda: auth)
    monkeypatch.setattr(chat_ws_module, "_get_security_manager", lambda: manager)
    monkeypatch.setattr(chat_ws_module, "_get_chat_service", lambda: service)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), service, auth, manager


def test_streams_chunks_then_final(monkeypatch):
    client, service, _, _ = _client(monkeypatch, authenticated=True)

    with client.websocket_connect(
        "/chat/ws", headers={"x-api-key": "k-123"}
    ) as websocket:
        websocket.send_json({"query": "hi there", "conversation_id": "c1"})
        first = websocket.receive_json()
        second = websocket.receive_json()
        final = websocket.receive_json()

    assert first == {"type": "chunk", "content": "Hello "}
    assert second == {"type": "chunk", "content": "world"}
    assert final["type"] == "final"
    assert service.requests[0].query == "hi there"
    assert service.requests[0].conversation_id == "c1"


def test_unauthenticated_handshake_closed_4401(monkeypatch):
    client, service, _, _ = _client(monkeypatch, authenticated=False)

    with pytest.raises(Exception):  # starlette raises on closed handshake
        with client.websocket_connect("/chat/ws") as websocket:
            websocket.receive_json()
    assert service.requests == []


def test_guest_role_is_forbidden_like_rest(monkeypatch):
    """REST chat admits user/admin/job/scoped only; GUEST must not drive the
    model over WebSocket either."""
    client, service, _, _ = _client(
        monkeypatch, authenticated=True, role=AuthRole.GUEST
    )

    with pytest.raises(Exception):
        with client.websocket_connect(
            "/chat/ws", headers={"x-api-key": "k"}
        ) as websocket:
            websocket.receive_json()
    assert service.requests == []


def test_rate_limited_handshake_is_rejected_before_accept(monkeypatch):
    client, service, _, manager = _client(monkeypatch, authenticated=True)
    manager.rate_limiter.check.side_effect = HTTPException(
        status_code=429, detail="Rate limit exceeded", headers={"Retry-After": "7"}
    )

    with pytest.raises(Exception):
        with client.websocket_connect(
            "/chat/ws", headers={"x-api-key": "k"}
        ) as websocket:
            websocket.receive_json()
    assert service.requests == []


def test_api_key_header_is_translated_to_apikey_scheme(monkeypatch):
    client, _, auth, _ = _client(monkeypatch, authenticated=True)

    with client.websocket_connect(
        "/chat/ws", headers={"x-api-key": "k-123"}
    ) as websocket:
        websocket.send_json({"query": "q"})
        websocket.receive_json()

    assert auth.headers_seen[0] == "ApiKey k-123"


def test_each_turn_is_metered_like_a_rest_request(monkeypatch):
    client, service, _, manager = _client(monkeypatch, authenticated=True, chunks=["a"])

    with client.websocket_connect("/chat/ws", headers={"x-api-key": "k"}) as websocket:
        for turn in ("first", "second", "third"):
            websocket.send_json({"query": turn})
            assert websocket.receive_json()["type"] == "chunk"
            assert websocket.receive_json()["type"] == "final"

    # One check at the handshake plus one per turn, all on the same identity
    # key the REST dependency would use.
    assert manager.rate_limiter.check.await_count == 4
    keys = {call.args[0] for call in manager.rate_limiter.check.await_args_list}
    assert len(keys) == 1
    assert [r.query for r in service.requests] == ["first", "second", "third"]


def test_rate_limited_turn_costs_an_error_frame_not_the_connection(monkeypatch):
    client, service, _, manager = _client(monkeypatch, authenticated=True, chunks=["a"])
    limited = HTTPException(
        status_code=429, detail="Rate limit exceeded", headers={"Retry-After": "3"}
    )
    # Handshake ok, first turn throttled, second turn ok.
    manager.rate_limiter.check.side_effect = [None, limited, None]

    with client.websocket_connect("/chat/ws", headers={"x-api-key": "k"}) as websocket:
        websocket.send_json({"query": "too fast"})
        frame = websocket.receive_json()
        assert frame["type"] == "error"
        assert frame["retry_after"] == "3"

        websocket.send_json({"query": "later"})
        assert websocket.receive_json()["type"] == "chunk"

    assert [r.query for r in service.requests] == ["later"]


def test_credential_revoked_mid_session_closes_the_socket(monkeypatch):
    client, service, auth, _ = _client(monkeypatch, authenticated=True, chunks=["a"])

    with client.websocket_connect("/chat/ws", headers={"x-api-key": "k"}) as websocket:
        websocket.send_json({"query": "ok"})
        assert websocket.receive_json()["type"] == "chunk"
        assert websocket.receive_json()["type"] == "final"

        auth._authenticated = False  # token revoked / expired between turns
        websocket.send_json({"query": "still me?"})
        with pytest.raises(Exception):
            websocket.receive_json()

    assert [r.query for r in service.requests] == ["ok"]


def test_empty_query_gets_error_frame_not_model_spend(monkeypatch):
    client, service, _, _ = _client(monkeypatch, authenticated=True)

    with client.websocket_connect(
        "/chat/ws", headers={"authorization": "Bearer tok"}
    ) as websocket:
        websocket.send_json({"query": ""})
        frame = websocket.receive_json()

    assert frame["type"] == "error"
    assert service.requests == []


def test_overlong_query_gets_error_frame_and_connection_survives(monkeypatch):
    # ChatRequest caps query at 8000 chars; a 9000-char frame must produce an
    # error frame (REST parity: rejected, not silently truncated) and the
    # connection must stay usable for the next turn.
    client, service, _, _ = _client(monkeypatch, authenticated=True)

    with client.websocket_connect("/chat/ws", headers={"x-api-key": "k"}) as websocket:
        websocket.send_json({"query": "q" * 9000})
        frame = websocket.receive_json()
        assert frame["type"] == "error"

        websocket.send_json({"query": "still alive?"})
        assert websocket.receive_json()["type"] == "chunk"

    assert [r.query for r in service.requests] == ["still alive?"]


def test_invalid_payload_never_kills_the_connection(monkeypatch):
    client, service, _, _ = _client(monkeypatch, authenticated=True)

    with client.websocket_connect("/chat/ws", headers={"x-api-key": "k"}) as websocket:
        websocket.send_json({"query": "x", "conversation_id": {"not": "a-string"}})
        assert websocket.receive_json()["type"] == "error"

        websocket.send_json({"query": "recovered"})
        assert websocket.receive_json()["type"] == "chunk"

    assert [r.query for r in service.requests] == ["recovered"]


def test_multiple_turns_on_one_connection(monkeypatch):
    client, service, _, _ = _client(monkeypatch, authenticated=True, chunks=["a"])

    with client.websocket_connect("/chat/ws", headers={"x-api-key": "k"}) as websocket:
        for turn in ("first", "second"):
            websocket.send_json({"query": turn})
            assert websocket.receive_json()["type"] == "chunk"
            assert websocket.receive_json()["type"] == "final"

    assert [r.query for r in service.requests] == ["first", "second"]
