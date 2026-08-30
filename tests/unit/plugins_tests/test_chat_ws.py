"""Tests for the WebSocket chat endpoint (/chat/ws).

SSE covers one-shot streaming; the WS endpoint adds a persistent
conversational channel. Auth happens at the handshake (Authorization header
or x-api-key — same credentials as the REST chat surface); an unauthenticated
handshake is closed with 4401 before any model spend.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plugins.api_routers.chat_ws as chat_ws_module
from core.auth.types import AuthRole, AuthUser
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
    def __init__(self, authenticated: bool) -> None:
        self._authenticated = authenticated
        self.headers_seen: list[str | None] = []

    async def authenticate(self, auth_header: str | None) -> AuthUser:
        self.headers_seen.append(auth_header)
        if self._authenticated and auth_header:
            return AuthUser(user_id="user-1", roles={AuthRole.USER})
        return AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})


def _client(monkeypatch, *, authenticated: bool, chunks: list[str] | None = None):
    service = _FakeChatService(chunks or ["Hello ", "world"])
    auth = _FakeAuthManager(authenticated)
    monkeypatch.setattr(chat_ws_module, "_get_chat_service", lambda: service)
    monkeypatch.setattr(chat_ws_module, "_get_auth_manager", lambda: auth)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), service, auth


def test_streams_chunks_then_final(monkeypatch):
    client, service, _ = _client(monkeypatch, authenticated=True)

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
    client, service, _ = _client(monkeypatch, authenticated=False)

    with pytest.raises(Exception):  # starlette raises on closed handshake
        with client.websocket_connect("/chat/ws") as websocket:
            websocket.receive_json()
    assert service.requests == []


def test_api_key_header_is_translated_to_apikey_scheme(monkeypatch):
    client, _, auth = _client(monkeypatch, authenticated=True)

    with client.websocket_connect(
        "/chat/ws", headers={"x-api-key": "k-123"}
    ) as websocket:
        websocket.send_json({"query": "q"})
        websocket.receive_json()

    assert auth.headers_seen[0] == "ApiKey k-123"


def test_empty_query_gets_error_frame_not_model_spend(monkeypatch):
    client, service, _ = _client(monkeypatch, authenticated=True)

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
    client, service, _ = _client(monkeypatch, authenticated=True)

    with client.websocket_connect("/chat/ws", headers={"x-api-key": "k"}) as websocket:
        websocket.send_json({"query": "q" * 9000})
        frame = websocket.receive_json()
        assert frame["type"] == "error"

        websocket.send_json({"query": "still alive?"})
        assert websocket.receive_json()["type"] == "chunk"

    assert [r.query for r in service.requests] == ["still alive?"]


def test_invalid_payload_never_kills_the_connection(monkeypatch):
    client, service, _ = _client(monkeypatch, authenticated=True)

    with client.websocket_connect("/chat/ws", headers={"x-api-key": "k"}) as websocket:
        websocket.send_json({"query": "x", "conversation_id": {"not": "a-string"}})
        assert websocket.receive_json()["type"] == "error"

        websocket.send_json({"query": "recovered"})
        assert websocket.receive_json()["type"] == "chunk"

    assert [r.query for r in service.requests] == ["recovered"]


def test_multiple_turns_on_one_connection(monkeypatch):
    client, service, _ = _client(monkeypatch, authenticated=True, chunks=["a"])

    with client.websocket_connect("/chat/ws", headers={"x-api-key": "k"}) as websocket:
        for turn in ("first", "second"):
            websocket.send_json({"query": turn})
            assert websocket.receive_json()["type"] == "chunk"
            assert websocket.receive_json()["type"] == "final"

    assert [r.query for r in service.requests] == ["first", "second"]
