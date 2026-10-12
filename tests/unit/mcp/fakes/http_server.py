"""An in-process fake MCP server over httpx.MockTransport (legacy era)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

TOOL = {"name": "echo", "description": "echo", "inputSchema": {"type": "object"}}


def rpc(rid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def json_response(body: Any, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


def sse_response(events: list[Any]) -> httpx.Response:
    body = "".join(f"event: message\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(
        200, content=body.encode(), headers={"content-type": "text/event-stream"}
    )


class EndlessSSE(httpx.AsyncByteStream):
    """Keep-alive comments forever, one every ``interval`` seconds."""

    def __init__(self, interval: float = 0.02) -> None:
        self.interval = interval
        self.chunks_sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            await asyncio.sleep(self.interval)
            self.chunks_sent += 1
            yield b": keep-alive\n\n"


class HugeBody(httpx.AsyncByteStream):
    """A ``total``-byte body served in 1 MiB chunks; counts what was pulled."""

    def __init__(self, total: int = 100 * 1024 * 1024) -> None:
        self.total = total
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        chunk = b"x" * (1 << 20)
        while self.sent < self.total:
            self.sent += len(chunk)
            yield chunk


Handler = Callable[[httpx.Request, dict[str, Any]], Awaitable[httpx.Response]]


def default_methods(request_body: dict[str, Any]) -> httpx.Response | None:
    method = request_body.get("method")
    rid = request_body.get("id")
    if method == "initialize":
        return json_response(
            rpc(
                rid,
                {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "fake", "version": "1"},
                    "capabilities": {"tools": {}},
                },
            ),
            **{"Mcp-Session-Id": "sess-1"},
        )
    if method == "server/discover":
        return json_response(
            {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}}
        )
    if method == "tools/list":
        return json_response(rpc(rid, {"tools": [TOOL]}))
    if method and rid is None:
        return httpx.Response(202)
    return None


def make_transport(handler: Handler | None = None) -> tuple[httpx.MockTransport, list]:
    """Return (transport, seen) where ``seen`` records every request body."""
    seen: list[tuple[httpx.Request, dict[str, Any]]] = []

    async def _handle(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = {}
        if request.content:
            parsed = json.loads(request.content)
            body = parsed if isinstance(parsed, dict) else {}
        seen.append((request, body))
        if handler is not None:
            return await handler(request, body)
        return default_methods(body) or httpx.Response(404)

    return httpx.MockTransport(_handle), seen
