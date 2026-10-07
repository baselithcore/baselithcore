"""Item 4 (HTTP): response cap, total deadline, allowed_hosts, no redirects, SSRF."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from core.mcp.client_errors import MCPTransportError
from core.mcp.http_client_transport import HTTPClientTransport

from .fakes.http_server import (
    EndlessSSE,
    HugeBody,
    json_response,
    make_transport,
    rpc,
    sse_response,
)

URL = "https://mcp.example.com/mcp"
PING = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


async def test_100mb_json_body_is_cut_off_at_the_cap(internal_ok: None) -> None:
    body = HugeBody()

    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=body
        )

    transport, _ = make_transport(handler)
    t = HTTPClientTransport(URL, httpx_transport=transport)
    with pytest.raises(MCPTransportError, match="too large"):
        await t.send(PING)
    assert body.sent <= 6 * (1 << 20)  # default cap 4 MiB: never pulled the 100 MB
    await t.close()


async def test_declared_content_length_over_cap_fails_without_reading(
    internal_ok: None,
) -> None:
    body = HugeBody(total=10 * (1 << 20))

    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-length": str(10 << 20),
            },
            stream=body,
        )

    transport, _ = make_transport(handler)
    t = HTTPClientTransport(URL, httpx_transport=transport, max_response_bytes=1 << 20)
    with pytest.raises(MCPTransportError, match="too large"):
        await t.send(PING)
    assert body.sent <= 1 << 20
    await t.close()


async def test_sse_accumulation_is_capped(internal_ok: None) -> None:
    class Flood(httpx.AsyncByteStream):
        sent = 0

        async def __aiter__(self) -> Any:
            while True:
                self.sent += 1
                yield b"data: " + b"y" * 4096 + b"\n\n"

    flood = Flood()

    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=flood
        )

    transport, _ = make_transport(handler)
    t = HTTPClientTransport(
        URL, httpx_transport=transport, max_response_bytes=64 * 1024
    )
    with pytest.raises(MCPTransportError, match="too large"):
        await t.send(PING)
    assert flood.sent < 100
    await t.close()


async def test_endless_sse_hits_the_total_deadline(internal_ok: None) -> None:
    stream = EndlessSSE(interval=0.02)

    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    transport, _ = make_transport(handler)
    t = HTTPClientTransport(URL, httpx_transport=transport, total_timeout=0.3)
    started = time.monotonic()
    with pytest.raises(MCPTransportError, match="deadline"):
        await t.send(PING)
    assert 0.25 <= time.monotonic() - started < 2.0
    await t.close()


async def test_normal_sse_reply_still_works(internal_ok: None) -> None:
    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        return sse_response([rpc(b["id"], {"ok": True})])

    transport, _ = make_transport(handler)
    t = HTTPClientTransport(URL, httpx_transport=transport)
    reply = await t.send(PING)
    assert reply == rpc(1, {"ok": True})
    await t.close()


async def test_default_cap_comes_from_config(internal_ok: None, mcp_env: Any) -> None:
    mcp_env(MCP_CLIENT_MAX_RESPONSE_BYTES="2048")

    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        return json_response(rpc(b["id"], {"blob": "z" * 10_000}))

    transport, _ = make_transport(handler)
    t = HTTPClientTransport(URL, httpx_transport=transport)
    with pytest.raises(MCPTransportError, match="too large"):
        await t.send(PING)
    await t.close()


def test_allowed_hosts_validated_at_construction(internal_ok: None) -> None:
    with pytest.raises(ValueError, match="allowed"):
        HTTPClientTransport(
            "https://evil.example.org/mcp", allowed_hosts={"mcp.example.com"}
        )
    HTTPClientTransport(URL, allowed_hosts=["mcp.example.com"])  # fine


async def test_redirects_are_never_followed(internal_ok: None) -> None:
    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            302, headers={"Location": "https://169.254.169.254/latest"}
        )

    transport, seen = make_transport(handler)
    t = HTTPClientTransport(URL, httpx_transport=transport)
    with pytest.raises(MCPTransportError, match="redirect"):
        await t.send(PING)
    assert len(seen) == 1
    await t.close()


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:9/mcp",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]:9/mcp",
        "http://localhost:9/mcp",
    ],
)
async def test_ssrf_targets_are_blocked(target: str) -> None:
    t = HTTPClientTransport(target)
    with pytest.raises(MCPTransportError, match="(?i)blocked|ssrf|not allowed"):
        await t.send(PING)
    await t.close()
