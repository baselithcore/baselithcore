"""Item 5: only result/error with no ``method`` and OUR id is the reply."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.mcp.client import MCPClient
from core.mcp.client_errors import MCPProtocolError
from core.mcp.reply_matching import is_reply
from core.mcp.stdio_client_transport import read_response

from .fakes.http_server import (
    default_methods,
    json_response,
    make_transport,
    rpc,
    sse_response,
)

STDIO = str(Path(__file__).parent / "fakes" / "stdio_server.py")
URL = "https://mcp.example.com/mcp"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"id": 1, "result": {}}, True),
        ({"id": 1, "error": {"code": 1, "message": "x"}}, True),
        ({"id": 1, "method": "sampling/createMessage", "params": {}}, False),
        ({"id": 1, "method": "x", "result": {}}, False),
        ({"id": 1}, False),
        ({"id": 2, "result": {}}, False),
        ({"id": True, "result": {}}, False),
        ({"id": "1", "result": {}}, False),
        ({"result": {}}, False),
        ({"method": "notifications/x"}, False),
    ],
)
def test_is_reply(message: dict[str, Any], expected: bool) -> None:
    assert is_reply(message, 1) is expected


class _Lines:
    def __init__(self, *messages: dict[str, Any]) -> None:
        self._lines = [json.dumps(m).encode() + b"\n" for m in messages]

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


async def test_stdio_read_response_skips_requests_carrying_our_id() -> None:
    reader = _Lines(
        {"jsonrpc": "2.0", "id": 5, "method": "roots/list"},
        {"jsonrpc": "2.0", "id": 4, "result": {"stale": True}},
        {"jsonrpc": "2.0", "id": 5, "result": {"real": True}},
    )
    got = await read_response(reader, 5, timeout=1.0)
    assert got["result"] == {"real": True}


async def test_http_json_body_that_is_a_server_request_is_not_the_reply(
    internal_ok: None,
) -> None:
    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        if b.get("method") == "tools/list":
            return json_response(
                {"jsonrpc": "2.0", "id": b["id"], "method": "roots/list", "params": {}}
            )
        return default_methods(b) or httpx.Response(404)

    transport, _ = make_transport(handler)
    async with MCPClient(url=URL, httpx_transport=transport) as client:
        with pytest.raises(MCPProtocolError):
            await client.list_tools()


async def test_http_sse_server_request_with_our_id_is_skipped(
    internal_ok: None,
) -> None:
    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        if b.get("method") == "tools/list":
            return sse_response(
                [
                    {
                        "jsonrpc": "2.0",
                        "id": b["id"],
                        "method": "sampling/createMessage",
                    },
                    {"jsonrpc": "2.0", "method": "notifications/progress"},
                    rpc(b["id"], {"tools": [{"name": "real"}]}),
                ]
            )
        # the client's answer to the server request (an error) lands here
        return default_methods(b) or httpx.Response(202)

    transport, seen = make_transport(handler)
    async with MCPClient(url=URL, httpx_transport=transport) as client:
        tools = await client.list_tools()
    assert [t.name for t in tools] == ["real"]
    answers = [b for _, b in seen if "error" in b and "method" not in b]
    assert answers and answers[0]["error"]["code"] == -32601


async def test_stdio_server_request_with_our_id_is_not_taken_as_the_reply() -> None:
    client = MCPClient(command=[sys.executable, STDIO])
    await client.connect(env={"FAKE_MODE": "ourid_request"})
    try:
        result = await asyncio.wait_for(client.call_tool("x", {}), timeout=10)
    finally:
        await client.disconnect()
    answered = result["client_answered"]
    assert "error" in answered and "result" not in answered
