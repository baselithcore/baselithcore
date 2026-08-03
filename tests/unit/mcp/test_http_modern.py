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


async def _param_app():
    server = MCPServer(name="test-server", version="1.0.0")

    async def execute_sql(region: str, query: str) -> str:
        return f"{region}:{query}"

    server.register_tool(
        name="execute_sql",
        description="Execute SQL",
        input_schema={
            "type": "object",
            "properties": {
                "region": {"type": "string", "x-mcp-header": "Region"},
                "query": {"type": "string"},
            },
            "required": ["region", "query"],
        },
        handler=execute_sql,
    )
    app = FastAPI()
    app.include_router(create_mcp_http_router(server, config=_config()))
    return app


def _sql_body():
    return _modern_body(
        "tools/call",
        {
            "name": "execute_sql",
            "arguments": {"region": "us-west1", "query": "SELECT 1"},
        },
    )


async def test_mirrored_param_header_must_be_present_and_match():
    app = await _param_app()
    async with _asgi_client(app) as client:
        missing = await client.post(
            "/mcp",
            json=_sql_body(),
            headers=_modern_headers("tools/call", "execute_sql"),
        )
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == -32020

        wrong = await client.post(
            "/mcp",
            json=_sql_body(),
            headers={
                **_modern_headers("tools/call", "execute_sql"),
                "Mcp-Param-Region": "eu-west1",
            },
        )
        assert wrong.status_code == 400

        ok = await client.post(
            "/mcp",
            json=_sql_body(),
            headers={
                **_modern_headers("tools/call", "execute_sql"),
                "Mcp-Param-Region": "us-west1",
            },
        )
        assert ok.status_code == 200
        assert ok.json()["result"]["content"][0]["text"] == "us-west1:SELECT 1"


# ---------------------------------------------------------------------------
# SSE response streams
# ---------------------------------------------------------------------------


async def _sse_app():
    from core.mcp import report_progress

    server = MCPServer(name="test-server", version="1.0.0")

    async def indexed() -> str:
        await report_progress(1, total=2, message="half")
        await report_progress(2, total=2)
        return "indexed"

    server.register_tool(
        name="indexed",
        description="Reports progress",
        input_schema={"type": "object", "properties": {}},
        handler=indexed,
    )
    app = FastAPI()
    app.include_router(create_mcp_http_router(server, config=_config()))
    return app, server


def _events(text: str):
    import json

    return [
        json.loads(line[len("data: ") :])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


async def test_progress_token_switches_the_response_to_a_stream():
    app, _ = await _sse_app()
    body = _modern_body("tools/call", {"name": "indexed", "arguments": {}})
    body["params"]["_meta"]["progressToken"] = "tok"

    async with _asgi_client(app) as client:
        response = await client.post(
            "/mcp", json=body, headers=_modern_headers("tools/call", "indexed")
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"

        events = _events(response.text)
        progress = [e for e in events if e.get("method") == "notifications/progress"]
        assert [p["params"]["progress"] for p in progress] == [1, 2]
        # The final response terminates the stream.
        assert events[-1]["result"]["content"][0]["text"] == "indexed"


async def test_plain_request_stays_a_json_body():
    app, _ = await _sse_app()
    async with _asgi_client(app) as client:
        response = await client.post(
            "/mcp",
            json=_modern_body("tools/list"),
            headers=_modern_headers("tools/list"),
        )

        assert response.headers["content-type"].startswith("application/json")


async def test_subscriptions_listen_streams_change_notifications():
    """The listen response *is* the stream: ack first, then what was asked for.

    httpx's ASGI transport buffers a response body to completion, so the
    stream is exercised end-to-end and then closed from the server side; the
    frames are asserted once it has terminated.
    """
    import asyncio

    app, server = await _sse_app()
    body = _modern_body(
        "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
    )

    async def change_then_close() -> None:
        for _ in range(200):
            if server._subscriptions.active:
                break
            await asyncio.sleep(0.005)

        async def later() -> str:
            return "ok"

        server.register_tool(
            name="later",
            description="",
            input_schema={"type": "object", "properties": {}},
            handler=later,
        )
        await asyncio.sleep(0.05)
        server._subscriptions.close_all()

    async with _asgi_client(app) as client:
        driver = asyncio.create_task(change_then_close())
        response = await asyncio.wait_for(
            client.post(
                "/mcp", json=body, headers=_modern_headers("subscriptions/listen")
            ),
            timeout=5,
        )
        await driver

    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert events[0]["method"] == "notifications/subscriptions/acknowledged"
    assert events[1]["method"] == "notifications/tools/list_changed"
    # Graceful closure: the listen request gets its own empty result last.
    assert events[-1]["id"] == 1
