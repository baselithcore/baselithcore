"""Modern-era (2026-07-28) Streamable HTTP: stateless and header-validated.

The revision removed protocol-level sessions and mirrors body fields into HTTP
headers, which the server must validate against the body. These tests cover
that path and confirm the legacy session flow still works beside it.
"""

import base64
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from core.mcp.http_transport import SESSION_HEADER, create_mcp_http_router
from core.mcp.server import MCPServer


def _config(**overrides):
    base = {
        "mcp_http_path": "/mcp",
        "mcp_http_require_auth": False,
        "mcp_http_session_ttl_seconds": 3600,
        "mcp_http_max_sessions_per_client": 0,
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


async def _handshake(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "t"}},
        },
    )
    assert response.status_code == 200
    return response.headers[SESSION_HEADER]


MODERN_VERSION = "2026-07-28"
_META = {
    "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _modern_body(method: str, params=None, msg_id=1):
    body = dict(params or {})
    body["_meta"] = dict(_META)
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": body}


def _modern_headers(method: str, name: str | None = None) -> dict[str, str]:
    headers = {"MCP-Protocol-Version": MODERN_VERSION, "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


async def test_modern_request_needs_no_session():
    """2026-07-28 removed sessions: a first request just works."""
    async with _asgi_client(_app()) as client:
        response = await client.post(
            "/mcp",
            json=_modern_body("tools/list"),
            headers=_modern_headers("tools/list"),
        )

        assert response.status_code == 200
        assert SESSION_HEADER not in response.headers
        assert response.json()["result"]["resultType"] == "complete"


async def test_missing_mcp_method_header_is_header_mismatch():
    async with _asgi_client(_app()) as client:
        response = await client.post(
            "/mcp",
            json=_modern_body("tools/list"),
            headers={"MCP-Protocol-Version": MODERN_VERSION},
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32020


async def test_mcp_method_header_must_match_body():
    async with _asgi_client(_app()) as client:
        response = await client.post(
            "/mcp",
            json=_modern_body("tools/list"),
            headers=_modern_headers("tools/call"),
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32020


async def test_protocol_version_header_must_match_body_meta():
    body = _modern_body("tools/list")
    async with _asgi_client(_app()) as client:
        response = await client.post(
            "/mcp",
            json=body,
            headers={
                "MCP-Protocol-Version": "2025-11-25",
                "Mcp-Method": "tools/list",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32020


async def test_mcp_name_header_required_and_validated():
    body = _modern_body("tools/call", {"name": "echo", "arguments": {"message": "hi"}})
    async with _asgi_client(_app()) as client:
        missing = await client.post(
            "/mcp", json=body, headers=_modern_headers("tools/call")
        )
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == -32020

        wrong = await client.post(
            "/mcp", json=body, headers=_modern_headers("tools/call", "other")
        )
        assert wrong.status_code == 400

        ok = await client.post(
            "/mcp", json=body, headers=_modern_headers("tools/call", "echo")
        )
        assert ok.status_code == 200


async def test_mcp_name_accepts_the_base64_sentinel():
    """Names outside the header-safe ASCII set travel Base64-encoded."""
    uri = "mcp://docs/città"
    encoded = base64.b64encode(uri.encode()).decode()
    body = _modern_body("resources/read", {"uri": uri})

    async with _asgi_client(_app()) as client:
        response = await client.post(
            "/mcp",
            json=body,
            headers=_modern_headers("resources/read", f"=?base64?{encoded}?="),
        )

        # The header matches the body, so validation passes and the request
        # reaches the handler (which reports the URI as unknown).
        assert response.status_code == 200
        assert response.json()["error"]["code"] == -32602


async def test_unknown_modern_method_is_404_with_method_not_found():
    async with _asgi_client(_app()) as client:
        response = await client.post(
            "/mcp",
            json=_modern_body("nope/nope"),
            headers=_modern_headers("nope/nope"),
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == -32601


async def test_unsupported_version_is_400_with_supported_list():
    body = _modern_body("tools/list")
    body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "1900-01-01"

    async with _asgi_client(_app()) as client:
        response = await client.post(
            "/mcp",
            json=body,
            headers={
                "MCP-Protocol-Version": "1900-01-01",
                "Mcp-Method": "tools/list",
            },
        )

        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == -32022
        assert MODERN_VERSION in error["data"]["supported"]


async def test_session_header_is_ignored_for_modern_requests():
    """A stale session id from an older client must not fail a modern request."""
    async with _asgi_client(_app()) as client:
        response = await client.post(
            "/mcp",
            json=_modern_body("tools/list"),
            headers={**_modern_headers("tools/list"), SESSION_HEADER: "stale"},
        )

        assert response.status_code == 200


async def test_legacy_session_flow_still_works():
    """Dual-era: the handshake path is untouched by the modern one."""
    async with _asgi_client(_app()) as client:
        session_id = await _handshake(client)
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={SESSION_HEADER: session_id},
        )

        assert response.status_code == 200
        assert "resultType" not in response.json()["result"]
