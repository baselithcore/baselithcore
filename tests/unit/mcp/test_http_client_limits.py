"""Item 4 (HTTP): response cap, total deadline, allowed_hosts, no redirects, SSRF."""

from __future__ import annotations

import gzip
import time
from typing import Any

import httpx
import pytest

from core.mcp.bounded_http import read_capped
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


class _CountingBody(httpx.AsyncByteStream):
    """A fixed body that records whether anyone pulled it."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.pulled = False

    async def __aiter__(self) -> Any:
        self.pulled = True
        yield self.payload


# 64 MiB of zeros gzip down to ~64 KiB: httpx would inflate the whole chunk
# before any byte counter sees it, so the cap must never meet a decoder.
_BOMB = gzip.compress(b"\0" * (64 << 20))


@pytest.mark.parametrize("content_type", ["application/json", "text/event-stream"])
async def test_a_compressed_body_is_refused_before_it_is_inflated(
    internal_ok: None, content_type: str
) -> None:
    body = _CountingBody(_BOMB)

    async def handler(req: httpx.Request, b: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": content_type, "content-encoding": "gzip"},
            stream=body,
        )

    transport, _ = make_transport(handler)
    t = HTTPClientTransport(URL, httpx_transport=transport, max_response_bytes=1 << 20)
    with pytest.raises(MCPTransportError, match="Content-Encoding"):
        await t.send(PING)
    assert not body.pulled
    await t.close()


async def test_requests_ask_for_an_uncompressed_body(internal_ok: None) -> None:
    transport, seen = make_transport(
        lambda req, b: _json(rpc(1, {"tools": []}))  # type: ignore[arg-type,return-value]
    )
    t = HTTPClientTransport(
        URL, httpx_transport=transport, headers={"accept-encoding": "br"}
    )
    await t.send(PING)
    assert seen[0][0].headers.get_list("accept-encoding") == ["identity"]
    await t.close()


async def _json(payload: Any) -> httpx.Response:
    return json_response(payload)


async def test_oauth_reads_refuse_a_compressed_body() -> None:
    body = _CountingBody(_BOMB)
    response = httpx.Response(200, headers={"content-encoding": "br"}, stream=body)
    with pytest.raises(MCPTransportError, match="Content-Encoding"):
        await read_capped(response, 1 << 20)
    assert not body.pulled
