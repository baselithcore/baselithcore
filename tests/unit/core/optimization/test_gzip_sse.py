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


async def _ndjson_app(scope, receive, send) -> None:
    """Minimal ASGI app emitting a streamed application/x-ndjson response."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/x-ndjson")],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b'{"token": "' + b"x" * 2000 + b'"}\n',
            "more_body": True,
        }
    )
    await send({"type": "http.response.body", "body": b"", "more_body": False})


@pytest.mark.asyncio
async def test_ndjson_response_not_gzipped() -> None:
    """A streamed NDJSON response must pass through uncompressed.

    A fetch-based NDJSON reader sends ``Accept-Encoding: gzip`` and no
    ``text/event-stream`` Accept, so the request-side bypass cannot catch it:
    the decision has to be made from the *response* content-type, inside the
    responder. Compressing it collapses the token stream into one delayed
    flush.
    """
    mw = SmartGzipMiddleware(_ndjson_app, minimum_size=500)
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.request"}

    scope = {
        "type": "http",
        "path": "/api/stream",
        "headers": [(b"accept", b"*/*"), (b"accept-encoding", b"gzip")],
    }
    await mw(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.lower(): v for k, v in start["headers"]}
    assert headers.get(b"content-encoding") != b"gzip"
    assert headers.get(b"content-type") == b"application/x-ndjson"

    body = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    assert body.startswith(b'{"token": "xxx')


@pytest.fixture(
    params=["modern", "legacy"], ids=["send_with_compression", "send_with_gzip"]
)
def starlette_hook_name(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """Run a test under both names Starlette has given the compressor hook.

    ``send_with_gzip`` up to 0.38, ``send_with_compression`` from 1.x. The
    ``legacy`` variant renames the hook on the installed parent so the suite
    reproduces, on a modern Starlette, the layout of the 0.38.6 deployment
    where the pass-through silently stopped matching.
    """
    if request.param == "modern":
        return "send_with_compression"
    from starlette.middleware import gzip as starlette_gzip

    owner = next(
        (
            cls
            for cls in starlette_gzip.GZipResponder.__mro__
            if "send_with_compression" in vars(cls)
        ),
        None,
    )
    if owner is None:
        pytest.skip("installed Starlette already uses the legacy hook name")
    hook = vars(owner)["send_with_compression"]
    monkeypatch.delattr(owner, "send_with_compression")
    monkeypatch.setattr(owner, "send_with_gzip", hook, raising=False)
    return "send_with_gzip"


async def _heartbeat_app(scope, receive, send) -> None:
    """Streamed NDJSON made of small frames — the shape of an ingest heartbeat."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/x-ndjson"),
                (b"x-accel-buffering", b"no"),
            ],
        }
    )
    for i in range(4):
        await send(
            {
                "type": "http.response.body",
                "body": b'{"phase": "heartbeat", "i": %d}\n' % i,
                "more_body": True,
            }
        )
    await send({"type": "http.response.body", "body": b"", "more_body": False})


def _gzip_scope() -> dict:
    return {
        "type": "http",
        "path": "/docheck/api/v1/policies/ingest/document/stream",
        "headers": [(b"accept", b"*/*"), (b"accept-encoding", b"gzip, deflate, br")],
    }


@pytest.mark.asyncio
async def test_streamed_frames_leave_verbatim_and_one_by_one(
    starlette_hook_name: str,
) -> None:
    """Every streamed frame must reach the transport as-is, when it is sent.

    The pass-through is worthless if it merely strips the ``Content-Encoding``
    header: what keeps a reverse proxy from timing out a minutes-long ingest
    is that each heartbeat frame is a real body write. Routed through the
    stock compressor instead, a 28-byte frame leaves as a 0-byte body message
    (the deflate buffer never flushes mid-stream) — measured on Starlette
    0.38.6 — so this asserts frame count, bytes and order, not just headers.
    """
    mw = SmartGzipMiddleware(_heartbeat_app, minimum_size=500)
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.request"}

    await mw(_gzip_scope(), receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert dict(start["headers"]).get(b"content-encoding") is None

    frames = [m["body"] for m in sent if m["type"] == "http.response.body"]
    assert frames == [b'{"phase": "heartbeat", "i": %d}\n' % i for i in range(4)] + [
        b""
    ]


async def _json_app(scope, receive, send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send(
        {"type": "http.response.body", "body": b'{"pad": "' + b"x" * 2000 + b'"}'}
    )


@pytest.mark.asyncio
async def test_plain_json_still_compressed_under_either_starlette_hook_name(
    starlette_hook_name: str,
) -> None:
    """The responder must find the parent's compressor under both of its names.

    An override keyed on one name matches nothing on the other and fails
    silently — that is how the streaming pass-through was disabled on a
    deployment running 0.38.6 while the tests stayed green on 1.x. Ordinary
    JSON must still be compressed through whichever hook exists.
    """
    mw = SmartGzipMiddleware(_json_app, minimum_size=500)
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.request"}

    await mw(_gzip_scope(), receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert dict(start["headers"]).get(b"content-encoding") == b"gzip"
