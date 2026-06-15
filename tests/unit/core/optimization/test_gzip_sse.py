"""SmartGzipMiddleware must never compress (buffer) Server-Sent Events."""

from __future__ import annotations

import pytest

from core.middleware.optimization import SmartGzipMiddleware


async def _sse_app(scope, receive, send) -> None:
    """Minimal ASGI app emitting a text/event-stream response."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        }
    )
    # A large body that would otherwise exceed minimum_size and be gzipped.
    await send(
        {"type": "http.response.body", "body": b"data: " + b"x" * 2000 + b"\n\n"}
    )


def _scope(accept: bytes) -> dict:
    return {
        "type": "http",
        "path": "/api/baselithcontrol/stream",
        "headers": [(b"accept", accept)],
    }


def test_accepts_event_stream_detection() -> None:
    assert SmartGzipMiddleware._accepts_event_stream(_scope(b"text/event-stream"))
    assert not SmartGzipMiddleware._accepts_event_stream(_scope(b"application/json"))


@pytest.mark.asyncio
async def test_sse_response_not_gzipped() -> None:
    mw = SmartGzipMiddleware(_sse_app, minimum_size=500)
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.request"}

    await mw(_scope(b"text/event-stream"), receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.lower(): v for k, v in start["headers"]}
    # Bypassed → no gzip encoding applied, stream passes through verbatim.
    assert headers.get(b"content-encoding") != b"gzip"
    assert headers.get(b"content-type") == b"text/event-stream"
