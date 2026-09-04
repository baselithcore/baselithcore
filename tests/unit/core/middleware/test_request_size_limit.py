"""Tests for ``RequestSizeLimitMiddleware`` streaming enforcement.

The ``Content-Length`` fast path is only half the guard: a chunked or
length-less body must be cut off the moment the running byte count crosses
the cap, *not* buffered to completion and rejected afterwards. Otherwise the
"limit" still lets a client force the process to materialise an arbitrarily
large body before the 413 is written.
"""

from __future__ import annotations

import pytest

from core.middleware.security_headers import RequestSizeLimitMiddleware


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "method": "POST",
        "path": "/upload",
        "headers": headers or [],
    }


def _chunked_receive(chunks: list[bytes]):
    """Yield ``chunks`` as ``http.request`` messages, then disconnect."""
    queue = list(chunks)

    async def receive():
        if queue:
            body = queue.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(queue)}
        return {"type": "http.disconnect"}

    return receive


def _headers(sent: list[dict]) -> dict[str, str]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {k.decode(): v.decode() for k, v in start["headers"]}


@pytest.mark.asyncio
async def test_streaming_body_stops_at_cap_before_app_buffers_it():
    """Chunks past the cap never reach the app; the 413 is emitted at once."""
    received_by_app: list[bytes] = []
    app_completed = False

    async def app(scope, receive, send):
        nonlocal app_completed
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            received_by_app.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        app_completed = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestSizeLimitMiddleware(app, max_bytes=10)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    # 4 + 4 = 8 bytes fit; the third chunk crosses the 10-byte cap.
    await middleware(
        _scope(), _chunked_receive([b"aaaa", b"bbbb", b"cccc", b"dddd"]), send
    )

    assert not app_completed, "app must not run to completion past the cap"
    assert received_by_app == [b"aaaa", b"bbbb"]
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413
    assert _headers(sent)["connection"] == "close"
    bodies = [m for m in sent if m["type"] == "http.response.body"]
    assert len(bodies) == 1 and b"too large" in bodies[0]["body"]


@pytest.mark.asyncio
async def test_app_swallowing_the_cutoff_still_gets_a_413():
    """A route that catches everything cannot turn the overflow into a 200."""

    async def app(scope, receive, send):
        try:
            while True:
                message = await receive()
                if message["type"] != "http.request" or not message.get("more_body"):
                    break
        except Exception:
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"swallowed"})

    middleware = RequestSizeLimitMiddleware(app, max_bytes=3)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(_scope(), _chunked_receive([b"xx", b"yy"]), send)

    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert [s["status"] for s in starts] == [413]
    assert all(b"swallowed" not in m.get("body", b"") for m in sent)


@pytest.mark.asyncio
async def test_body_within_cap_streams_through_untouched():
    seen: list[bytes] = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            seen.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestSizeLimitMiddleware(app, max_bytes=10)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(_scope(), _chunked_receive([b"aaaa", b"bbbb"]), send)

    assert seen == [b"aaaa", b"bbbb"]
    assert next(m for m in sent if m["type"] == "http.response.start")["status"] == 200


@pytest.mark.asyncio
async def test_content_length_over_cap_rejected_without_reading_body():
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        return {"type": "http.request", "body": b"x" * 100, "more_body": False}

    async def app(scope, receive, send):  # pragma: no cover - must not run
        raise AssertionError("app must not be invoked")

    middleware = RequestSizeLimitMiddleware(app, max_bytes=10)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(_scope([(b"content-length", b"100")]), receive, send)

    assert calls == 0
    assert next(m for m in sent if m["type"] == "http.response.start")["status"] == 413
    assert _headers(sent)["connection"] == "close"


@pytest.mark.asyncio
async def test_disabled_cap_passes_everything():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestSizeLimitMiddleware(app, max_bytes=0)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await middleware(
        _scope([(b"content-length", b"999999")]), _chunked_receive([]), send
    )
    assert next(m for m in sent if m["type"] == "http.response.start")["status"] == 200
