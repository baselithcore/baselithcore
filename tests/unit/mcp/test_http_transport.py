"""Tests for the MCP Streamable HTTP transport (server + client sides)."""

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
        "http_allowed_origin_set": frozenset(),
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
        assert response.headers["WWW-Authenticate"] == "Bearer"


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
    session_id = store.create()
    assert store.touch(session_id) is True
    assert store.terminate(session_id) is True
    assert store.touch(session_id) is False
    assert store.terminate(session_id) is False


def test_session_store_expiry():
    store = SessionStore(ttl_seconds=-1.0)  # everything is instantly expired
    session_id = store.create()
    assert store.touch(session_id) is False


# ---------------------------------------------------------------------------
# Client transport (end-to-end against the ASGI app)
# ---------------------------------------------------------------------------


async def test_client_transport_end_to_end():
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
