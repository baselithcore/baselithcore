"""Tests for the MCP Streamable HTTP transport (server + client sides)."""

import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from core.mcp.http_client_transport import HTTPClientTransport
from core.mcp.http_transport import (
    PROTOCOL_HEADER,
    SESSION_HEADER,
    SessionStore,
    create_mcp_http_router,
)
from core.mcp.server import MCPServer


def _config(**overrides):
    base = {
        "mcp_http_path": "/mcp",
        "mcp_http_require_auth": False,
        "mcp_http_session_ttl_seconds": 3600,
        "mcp_http_max_sessions_per_client": 0,  # 0 = uncapped (default in tests)
        "http_allowed_origin_set": frozenset(),
        "authorization_server_list": (),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _server() -> MCPServer:
    server = MCPServer(name="test-server", version="1.0.0")

    @server.tool(name="echo", description="Echo a message")
    async def echo(message: str) -> str:
        return f"Echo: {message}"

    return server


def _app(config=None) -> FastAPI:
    app = FastAPI()
    app.include_router(create_mcp_http_router(_server(), config=config or _config()))
    return app


def _asgi_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://mcp.test"
    )


def _initialize_msg(msg_id=1):
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "t"}},
    }


async def _handshake(client: httpx.AsyncClient) -> str:
    response = await client.post("/mcp", json=_initialize_msg())
    assert response.status_code == 200
    session_id = response.headers[SESSION_HEADER]
    assert response.json()["result"]["protocolVersion"] == "2025-06-18"
    return session_id


# ---------------------------------------------------------------------------
# Server endpoint
# ---------------------------------------------------------------------------


async def test_initialize_mints_session_and_negotiates_version():
    async with _asgi_client(_app()) as client:
        session_id = await _handshake(client)
        assert len(session_id) > 20


async def test_request_without_session_is_404():
    async with _asgi_client(_app()) as client:
        response = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        assert response.status_code == 404


async def test_full_tool_flow_over_http():
    async with _asgi_client(_app()) as client:
        session_id = await _handshake(client)
        headers = {SESSION_HEADER: session_id, PROTOCOL_HEADER: "2025-06-18"}

        # notifications/initialized -> 202, no body
        note = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
        )
        assert note.status_code == 202

        listed = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=headers,
        )
        assert listed.status_code == 200
        tools = listed.json()["result"]["tools"]
        assert [t["name"] for t in tools] == ["echo"]

        called = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"message": "hi"}},
            },
            headers=headers,
        )
        assert called.status_code == 200
        content = called.json()["result"]["content"]
        assert content[0]["text"] == "Echo: hi"


async def test_batch_rejected_and_bad_protocol_version():
    async with _asgi_client(_app()) as client:
        session_id = await _handshake(client)

        batch = await client.post("/mcp", json=[_initialize_msg(1)])
        assert batch.status_code == 400

        bad_version = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={SESSION_HEADER: session_id, PROTOCOL_HEADER: "1999-01-01"},
        )
        assert bad_version.status_code == 400


async def test_delete_terminates_session():
    async with _asgi_client(_app()) as client:
        session_id = await _handshake(client)
        headers = {SESSION_HEADER: session_id}

        assert (await client.delete("/mcp", headers=headers)).status_code == 204
        # Session gone: further use is 404, double delete is 404.
        follow_up = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=headers,
        )
        assert follow_up.status_code == 404
        assert (await client.delete("/mcp", headers=headers)).status_code == 404


async def test_get_is_405():
    async with _asgi_client(_app()) as client:
        response = await client.get("/mcp")
        assert response.status_code == 405
        assert "POST" in response.headers["Allow"]


async def test_origin_allowlist():
    config = _config(http_allowed_origin_set=frozenset({"https://ok.example"}))
    async with _asgi_client(_app(config)) as client:
        denied = await client.post(
            "/mcp", json=_initialize_msg(), headers={"Origin": "https://evil.example"}
        )
        assert denied.status_code == 403

        allowed = await client.post(
            "/mcp", json=_initialize_msg(), headers={"Origin": "https://ok.example"}
        )
        assert allowed.status_code == 200


async def test_parse_error_is_400():
    async with _asgi_client(_app()) as client:
        response = await client.post(
            "/mcp", content=b"not-json", headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32700


# ---------------------------------------------------------------------------
# Authorization gate
# ---------------------------------------------------------------------------


class _StubAuthManager:
    def __init__(self, user):
        self._user = user

    async def authenticate(self, auth_header):
        self.seen_header = auth_header
        return self._user


async def test_auth_required_rejects_anonymous(monkeypatch):
    import core.auth.manager as auth_manager_module

    anonymous = SimpleNamespace(user_id="anonymous", is_authenticated=False)
    monkeypatch.setattr(
        auth_manager_module, "get_auth_manager", lambda: _StubAuthManager(anonymous)
    )
    config = _config(mcp_http_require_auth=True)
    async with _asgi_client(_app(config)) as client:
        response = await client.post("/mcp", json=_initialize_msg())
        assert response.status_code == 401
        # RFC 9728: the challenge points at the protected-resource metadata so
        # the client can discover which authorization server to use.
        challenge = response.headers["WWW-Authenticate"]
        assert challenge.startswith("Bearer ")
        assert (
            'resource_metadata="http://mcp.test/.well-known/'
            'oauth-protected-resource/mcp"' in challenge
        )


# ---------------------------------------------------------------------------
# Protected-resource metadata (RFC 9728)
# ---------------------------------------------------------------------------


async def test_protected_resource_metadata_is_served():
    config = _config(
        mcp_http_require_auth=True,
        authorization_server_list=("https://idp.example.com",),
    )
    async with _asgi_client(_app(config)) as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")

        assert response.status_code == 200
        body = response.json()
        assert body["resource"] == "http://mcp.test/mcp"
        assert body["authorization_servers"] == ["https://idp.example.com"]
        assert body["bearer_methods_supported"] == ["header"]


async def test_protected_resource_metadata_root_alias():
    """RFC 9728 clients that drop the path suffix still find the document."""
    config = _config(
        mcp_http_require_auth=True,
        authorization_server_list=("https://idp.example.com",),
    )
    async with _asgi_client(_app(config)) as client:
        response = await client.get("/.well-known/oauth-protected-resource")

        assert response.status_code == 200
        assert response.json()["resource"] == "http://mcp.test/mcp"


async def test_metadata_omits_unknown_authorization_servers():
    config = _config(mcp_http_require_auth=True, authorization_server_list=())
    async with _asgi_client(_app(config)) as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")

        assert response.status_code == 200
        assert "authorization_servers" not in response.json()


async def test_metadata_absent_when_auth_disabled():
    """Nothing to discover on an unauthenticated endpoint."""
    async with _asgi_client(_app(_config())) as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")

        assert response.status_code == 404


async def test_auth_required_accepts_authenticated(monkeypatch):
    import core.auth.manager as auth_manager_module

    user = SimpleNamespace(user_id="user-1", is_authenticated=True)
    stub = _StubAuthManager(user)
    monkeypatch.setattr(auth_manager_module, "get_auth_manager", lambda: stub)
    config = _config(mcp_http_require_auth=True)
    async with _asgi_client(_app(config)) as client:
        response = await client.post(
            "/mcp",
            json=_initialize_msg(),
            headers={"Authorization": "Bearer token-123"},
        )
        assert response.status_code == 200
        assert stub.seen_header == "Bearer token-123"


# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------


def test_session_store_lifecycle():
    store = SessionStore(ttl_seconds=3600)
    session_id = store.create("u1")
    assert store.touch(session_id, "u1") is True
    assert store.terminate(session_id, "u1") is True
    assert store.touch(session_id, "u1") is False
    assert store.terminate(session_id, "u1") is False


def test_session_store_expiry():
    store = SessionStore(ttl_seconds=-1.0)  # everything is instantly expired
    session_id = store.create("u1")
    assert store.touch(session_id, "u1") is False


def test_session_store_rejects_foreign_owner():
    # A session id presented by a different identity must not be usable.
    store = SessionStore(ttl_seconds=3600)
    session_id = store.create("alice")
    assert session_id is not None
    assert store.touch(session_id, "mallory") is False
    assert store.terminate(session_id, "mallory") is False
    # The real owner is unaffected.
    assert store.touch(session_id, "alice") is True


def test_session_store_per_owner_cap():
    store = SessionStore(ttl_seconds=3600, max_per_owner=2)
    assert store.create("u1") is not None
    assert store.create("u1") is not None
    assert store.create("u1") is None  # cap reached for u1
    # A different identity is unaffected by u1's cap.
    assert store.create("u2") is not None


async def test_cross_identity_session_rejected_over_http(monkeypatch):
    import core.auth.manager as auth_manager_module

    users = {
        "Bearer a": SimpleNamespace(user_id="alice", is_authenticated=True),
        "Bearer m": SimpleNamespace(user_id="mallory", is_authenticated=True),
    }

    class _MapAuth:
        async def authenticate(self, header):
            return users.get(header)

    monkeypatch.setattr(auth_manager_module, "get_auth_manager", lambda: _MapAuth())
    config = _config(mcp_http_require_auth=True)
    async with _asgi_client(_app(config)) as client:
        init = await client.post(
            "/mcp", json=_initialize_msg(), headers={"Authorization": "Bearer a"}
        )
        session_id = init.headers[SESSION_HEADER]
        # mallory presents alice's session id -> 404 (no takeover).
        stolen = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={
                SESSION_HEADER: session_id,
                PROTOCOL_HEADER: "2025-06-18",
                "Authorization": "Bearer m",
            },
        )
        assert stolen.status_code == 404


async def test_session_limit_exceeded_returns_429():
    config = _config(mcp_http_max_sessions_per_client=1)
    async with _asgi_client(_app(config)) as client:
        first = await client.post("/mcp", json=_initialize_msg(1))
        assert first.status_code == 200
        second = await client.post("/mcp", json=_initialize_msg(2))
        assert second.status_code == 429


# ---------------------------------------------------------------------------
# Client transport (end-to-end against the ASGI app)
# ---------------------------------------------------------------------------


async def test_client_transport_end_to_end(monkeypatch):
    # ASGITransport never touches the network, but the hardened client still
    # resolves the host to pin the connection (SSRF guard, Task 4 "Fase 1 -
    # SSRF unificato"). "mcp.test" is not a real domain, so fake DNS to a
    # public IP rather than loosening the guard for the test.
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    app = _app()
    transport = HTTPClientTransport(
        "http://mcp.test/mcp", httpx_transport=httpx.ASGITransport(app=app)
    )
    try:
        result = await transport.initialize(
            {"protocolVersion": "2025-06-18", "clientInfo": {"name": "c"}}
        )
        assert result["serverInfo"]["name"] == "test-server"

        note = await transport.send(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        assert note is None  # 202 accepted

        listed = await transport.send(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        assert [t["name"] for t in listed["result"]["tools"]] == ["echo"]
    finally:
        await transport.close()


async def test_mcp_client_http_branch_uses_transport():
    from core.mcp.client import MCPClient

    class _FakeTransport:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(message)
            if message.get("method") == "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"tools": [{"name": "t", "inputSchema": {}}]},
                }
            return None

        async def close(self):
            self.closed = True

    client = MCPClient(url="http://example/mcp")
    client._http = _FakeTransport()
    client._connected = True

    tools = await client.list_tools()
    assert [t.name for t in tools] == ["t"]

    await client.disconnect()
    assert client._http is None


def test_sse_parsing_matches_request_id():
    body = (
        'data: {"jsonrpc":"2.0","method":"noise"}\n\n'
        'data: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n\n'
    )
    parsed = HTTPClientTransport._parse_sse(body, 7)
    assert parsed["result"] == {"ok": True}
    # Without a matching id the last decoded event wins.
    fallback = HTTPClientTransport._parse_sse(body, None)
    assert fallback["id"] == 7


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
