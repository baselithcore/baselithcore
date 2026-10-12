"""Item 3: typed client errors, backward compatible with ``except RuntimeError``."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from baselith.core.mcp.client import MCPClient
from baselith.core.mcp.client_errors import (
    MCPAuthError,
    MCPClientError,
    MCPProtocolError,
    MCPToolError,
    MCPTransportError,
)

from .fakes.http_server import default_methods, json_response, make_transport

URL = "https://mcp.example.com/mcp"
SECRET = "sk-live-ABCDEFGHIJKLMNOP1234567890"  # gitleaks:allow (fake fixture)
METADATA = "https://mcp.example.com/.well-known/oauth-protected-resource"


def test_hierarchy_keeps_runtime_error_callers_working() -> None:
    for exc in (MCPAuthError, MCPTransportError, MCPProtocolError, MCPToolError):
        assert issubclass(exc, MCPClientError)
        assert issubclass(exc, RuntimeError)


async def _client_failing(status: int, headers: dict[str, str], body: str) -> MCPClient:
    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        if b.get("method") == "tools/list":
            return httpx.Response(status, headers=headers, content=body.encode())
        return default_methods(b) or httpx.Response(404)

    transport, _ = make_transport(handler)
    client = MCPClient(url=URL, httpx_transport=transport)
    await client.connect()
    return client


async def test_401_is_an_auth_error_with_challenge_details(internal_ok: None) -> None:
    challenge = f'Bearer realm="mcp", resource_metadata="{METADATA}"'
    client = await _client_failing(
        401, {"WWW-Authenticate": challenge}, f"token {SECRET}"
    )
    with pytest.raises(MCPAuthError) as info:
        await client.list_tools()
    err = info.value
    assert err.status_code == 401
    assert err.resource_metadata == METADATA
    assert err.www_authenticate is not None and "Bearer" in err.www_authenticate
    assert SECRET not in str(err)
    assert isinstance(err, RuntimeError)


async def test_403_is_an_auth_error_without_metadata(internal_ok: None) -> None:
    client = await _client_failing(403, {}, "nope")
    with pytest.raises(MCPAuthError) as info:
        await client.list_tools()
    assert info.value.status_code == 403
    assert info.value.resource_metadata is None
    assert info.value.www_authenticate is None


async def test_server_error_is_transport_error_with_no_raw_body(
    internal_ok: None,
) -> None:
    client = await _client_failing(500, {}, f"Traceback ... Bearer {SECRET}")
    with pytest.raises(MCPTransportError) as info:
        await client.list_tools()
    assert info.value.status_code == 500
    assert SECRET not in str(info.value)
    assert "Traceback" not in str(info.value) or len(str(info.value)) < 400


async def test_huge_error_body_is_bounded_in_the_message(internal_ok: None) -> None:
    client = await _client_failing(502, {}, "A" * 100_000)
    with pytest.raises(MCPTransportError) as info:
        await client.list_tools()
    assert len(str(info.value)) < 500


async def test_jsonrpc_error_is_a_protocol_error(internal_ok: None) -> None:
    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        if b.get("method") == "tools/list":
            return json_response(
                {
                    "jsonrpc": "2.0",
                    "id": b["id"],
                    "error": {
                        "code": -32602,
                        "message": f"bad Bearer {SECRET} " + "x" * 5000,
                    },
                }
            )
        return default_methods(b) or httpx.Response(404)

    transport, _ = make_transport(handler)
    async with MCPClient(url=URL, httpx_transport=transport) as client:
        with pytest.raises(MCPProtocolError, match="MCP error -32602") as info:
            await client.list_tools()
    assert info.value.code == -32602
    assert SECRET not in str(info.value)
    assert len(str(info.value)) < 500


async def test_auth_failure_during_connect_surfaces_as_auth_error(
    internal_ok: None,
) -> None:
    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        return httpx.Response(401, headers={"WWW-Authenticate": "Bearer"})

    transport, _ = make_transport(handler)
    with pytest.raises(MCPAuthError):
        await MCPClient(url=URL, httpx_transport=transport).connect()
