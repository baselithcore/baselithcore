"""Item 7: sampling/elicitation/roots are denied unless an input_provider is set."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx

from core.mcp.client import MCPClient

from .fakes.http_server import (
    default_methods,
    make_transport,
    rpc,
    sse_response,
)

STDIO = str(Path(__file__).parent / "fakes" / "stdio_server.py")
URL = "https://mcp.example.com/mcp"


async def test_stdio_sampling_without_provider_is_answered_with_an_error() -> None:
    client = MCPClient(command=[sys.executable, STDIO])
    await client.connect(env={"FAKE_MODE": "sampling"})
    try:
        result = await client.call_tool("x", {})
    finally:
        await client.disconnect()
    answered = result["client_answered"]
    assert answered["id"] == 777
    assert answered["error"]["code"] == -32601
    assert "result" not in answered


async def test_stdio_sampling_with_provider_is_fulfilled() -> None:
    seen: list[dict[str, Any]] = []

    async def provider(requests: dict[str, Any]) -> dict[str, Any]:
        seen.append(requests)
        return {
            k: {"role": "assistant", "content": {"type": "text", "text": "hi"}}
            for k in requests
        }

    client = MCPClient(command=[sys.executable, STDIO], input_provider=provider)
    await client.connect(env={"FAKE_MODE": "sampling"})
    try:
        result = await client.call_tool("x", {})
    finally:
        await client.disconnect()
    answered = result["client_answered"]
    assert answered["id"] == 777 and answered["result"]["role"] == "assistant"
    [(key, req)] = next(iter(seen)).items()
    assert req["method"] == "sampling/createMessage"


async def test_capabilities_are_not_advertised_without_a_provider(
    internal_ok: None,
) -> None:
    transport, seen = make_transport(None)
    async with MCPClient(url=URL, httpx_transport=transport) as client:
        assert client.client_capabilities == {}
    init = next(b for _, b in seen if b.get("method") == "initialize")
    assert init["params"]["capabilities"] == {}


async def test_capabilities_are_advertised_with_a_provider_on_the_legacy_path(
    internal_ok: None,
) -> None:
    async def provider(requests: dict[str, Any]) -> dict[str, Any]:
        return {}

    transport, seen = make_transport(None)
    async with MCPClient(url=URL, httpx_transport=transport, input_provider=provider):
        pass
    init = next(b for _, b in seen if b.get("method") == "initialize")
    assert set(init["params"]["capabilities"]) == {"elicitation", "sampling", "roots"}


async def test_http_stream_server_requests_get_error_responses(
    internal_ok: None,
) -> None:
    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        if b.get("method") == "tools/list":
            return sse_response(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": 41,
                        "method": "elicitation/create",
                        "params": {},
                    },
                    {"jsonrpc": "2.0", "id": 42, "method": "roots/list"},
                    {"jsonrpc": "2.0", "id": 43, "method": "ping"},
                    {"jsonrpc": "2.0", "id": 44, "method": "unknown/thing"},
                    rpc(b["id"], {"tools": []}),
                ]
            )
        return default_methods(b) or httpx.Response(202)

    transport, seen = make_transport(handler)
    async with MCPClient(url=URL, httpx_transport=transport) as client:
        await client.list_tools()
    replies = {b["id"]: b for _, b in seen if "method" not in b and "id" in b}
    assert replies[41]["error"]["code"] == -32601
    assert replies[42]["error"]["code"] == -32601
    assert replies[43]["result"] == {}
    assert replies[44]["error"]["code"] == -32601


async def test_provider_only_fulfils_advertised_capabilities(internal_ok: None) -> None:
    called = {"n": 0}

    async def provider(requests: dict[str, Any]) -> dict[str, Any]:
        called["n"] += 1
        return {k: {} for k in requests}

    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        if b.get("method") == "tools/list":
            return sse_response(
                [
                    {"jsonrpc": "2.0", "id": 50, "method": "roots/list"},
                    rpc(b["id"], {"tools": []}),
                ]
            )
        return default_methods(b) or httpx.Response(202)

    transport, seen = make_transport(handler)
    async with MCPClient(
        url=URL,
        httpx_transport=transport,
        input_provider=provider,
        client_capabilities={"sampling": {}},
    ) as client:
        await client.list_tools()
    assert called["n"] == 0
    reply = next(b for _, b in seen if b.get("id") == 50 and "method" not in b)
    assert reply["error"]["code"] == -32601
