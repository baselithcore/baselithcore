"""Tests for ``RequestIdMiddleware``: ``X-Request-ID`` validation and propagation."""

from __future__ import annotations

import re

import pytest

from core.middleware.observability import RequestIdMiddleware
from core.observability.setup import request_id_ctx

_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


async def _run(header_value: bytes | None) -> tuple[str, str]:
    """Drive the middleware; return (echoed response header, bound contextvar)."""
    seen: dict[str, str] = {}

    async def downstream(scope, receive, send):
        seen["ctx"] = request_id_ctx.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestIdMiddleware(downstream)
    headers = [] if header_value is None else [(b"x-request-id", header_value)]
    scope = {"type": "http", "method": "GET", "path": "/", "headers": headers}
    sent: list = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    echoed = dict(start["headers"])[b"x-request-id"].decode("latin-1")
    return echoed, seen["ctx"]


async def test_valid_incoming_id_is_echoed_and_bound():
    echoed, ctx = await _run(b"req-ABC.123_x")
    assert echoed == "req-ABC.123_x"
    assert ctx == "req-ABC.123_x"


async def test_missing_id_generates_uuid4():
    echoed, ctx = await _run(None)
    assert _UUID4.match(echoed), echoed
    assert ctx == echoed


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(b"a" * 129, id="too-long"),
        pytest.param(b"id with space", id="whitespace"),
        pytest.param(b"id\r\nX-Injected: 1", id="crlf"),
        pytest.param(b"id;drop", id="punctuation"),
        pytest.param("café".encode("latin-1"), id="non-ascii"),
        pytest.param(b"", id="empty"),
    ],
)
async def test_invalid_incoming_id_is_replaced(bad: bytes):
    """A caller-supplied id outside the safe alphabet never reaches logs or headers."""
    echoed, ctx = await _run(bad)
    assert _UUID4.match(echoed), echoed
    assert ctx == echoed


async def test_max_length_id_is_accepted():
    value = b"x" * 128
    echoed, _ = await _run(value)
    assert echoed == value.decode()
