"""Tests for the BaselithCore Python SDK using httpx.MockTransport."""

import json

import httpx
import pytest
from baselith_sdk import (
    AsyncBaselithClient,
    AuthenticationError,
    BaselithClient,
    NotFoundError,
    PermissionError_,
    ServerError,
)
from baselith_sdk.client import ChatStreamError, _aiter_sse_chunks, _iter_sse_chunks
from baselith_sdk.errors import APIConnectionError, BaselithConfigError

BASE = "https://api.test"


def _json_response(request, payload, status=200, headers=None):
    return httpx.Response(status, json=payload, headers=headers or {})


# === Construction ===
def test_requires_base_url():
    with pytest.raises(BaselithConfigError):
        BaselithClient("")


def test_versioned_url_routing():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return _json_response(request, {"answer": "hi"})

    with BaselithClient(BASE, api_key="k", transport=httpx.MockTransport(handler)) as c:
        c.chat("hello")
    assert captured["url"] == f"{BASE}/v1/chat"


def test_unversioned_health_path():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return _json_response(request, {"status": "ok"})

    with BaselithClient(BASE, transport=httpx.MockTransport(handler)) as c:
        c.health()
    assert captured["url"] == f"{BASE}/health"


# === Auth headers ===
def test_api_key_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return _json_response(request, {"answer": "x"})

    with BaselithClient(
        BASE, api_key="sk-123", transport=httpx.MockTransport(handler)
    ) as c:
        c.chat("q")
    assert captured["headers"]["x-api-key"] == "sk-123"
    assert "authorization" not in captured["headers"]


def test_bearer_token_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return _json_response(request, {"answer": "x"})

    with BaselithClient(
        BASE, bearer_token="jwt-abc", transport=httpx.MockTransport(handler)
    ) as c:
        c.chat("q")
    assert captured["headers"]["authorization"] == "Bearer jwt-abc"


def test_tenant_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return _json_response(request, {"answer": "x"})

    with BaselithClient(
        BASE, api_key="k", tenant_id="acme", transport=httpx.MockTransport(handler)
    ) as c:
        c.chat("q")
    assert captured["headers"]["x-tenant-id"] == "acme"


# === Chat ===
def test_chat_returns_typed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["query"] == "hello"
        return _json_response(
            request,
            {"answer": "world", "conversation_id": "c1", "sources": [{"id": 1}]},
        )

    with BaselithClient(BASE, api_key="k", transport=httpx.MockTransport(handler)) as c:
        resp = c.chat("hello")
    assert resp.answer == "world"
    assert resp.conversation_id == "c1"
    assert resp.sources == [{"id": 1}]


def test_chat_stream_yields_chunks():
    """Real server SSE frames decode to their plain-text payload, sans [DONE]."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = "data: Hello\n\ndata:  world\n\nevent: done\ndata: [DONE]\n\n"
        return httpx.Response(200, text=body)

    with BaselithClient(BASE, api_key="k", transport=httpx.MockTransport(handler)) as c:
        chunks = list(c.chat_stream("q"))
    assert "".join(chunks) == "Hello world"


def test_chat_stream_tolerates_keepalive_and_multiline():
    """A ``: keepalive`` comment is ignored; a multi-line chunk reassembles."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            ": keepalive\n\n"
            "data: line1\ndata: line2\n\n"
            ": keepalive\n\n"
            "event: done\ndata: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    with BaselithClient(BASE, api_key="k", transport=httpx.MockTransport(handler)) as c:
        chunks = list(c.chat_stream("q"))
    assert chunks == ["line1\nline2"]


def test_chat_stream_surfaces_error_event():
    """``event: error`` raises ``ChatStreamError`` instead of leaking as text."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = "data: partial\n\nevent: error\ndata: stream failed\n\nevent: done\ndata: [DONE]\n\n"
        return httpx.Response(200, text=body)

    with BaselithClient(BASE, api_key="k", transport=httpx.MockTransport(handler)) as c:
        gen = c.chat_stream("q")
        assert next(gen) == "partial"
        with pytest.raises(ChatStreamError) as ei:
            next(gen)
    assert "stream failed" in str(ei.value)


def test_iter_sse_chunks_handles_events_split_across_reads():
    """White-box: the decoder buffers a frame split across arbitrary reads."""
    raw = ["data: hel", "lo\n", "\nevent: don", "e\ndata: [DONE]\n\n"]
    assert list(_iter_sse_chunks(iter(raw))) == ["hello"]


def test_iter_sse_chunks_merges_crlf_split_exactly_at_the_boundary():
    """Regression: a read boundary between a "\\r" and its "\\n" must not
    fragment one logical ``data:`` block into two."""
    raw = ["data: hello\r", "\ndata: world\r\n\r\n"]
    assert list(_iter_sse_chunks(iter(raw))) == ["hello\nworld"]


@pytest.mark.asyncio
async def test_aiter_sse_chunks_merges_crlf_split_exactly_at_the_boundary():
    """Async counterpart of the CRLF-boundary regression above."""

    async def raw():
        for piece in ["data: hello\r", "\ndata: world\r\n\r\n"]:
            yield piece

    assert [c async for c in _aiter_sse_chunks(raw())] == ["hello\nworld"]


# === Feedback + idempotency ===
def test_feedback_sends_idempotency_key():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["idem"] = request.headers.get("Idempotency-Key")
        return _json_response(request, {"status": "ok"})

    with BaselithClient(BASE, api_key="k", transport=httpx.MockTransport(handler)) as c:
        out = c.submit_feedback(query="q", answer="a", feedback="positive")
    assert out["status"] == "ok"
    assert captured["idem"]  # auto-generated, non-empty


def test_feedback_respects_explicit_idempotency_key():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["idem"] = request.headers.get("Idempotency-Key")
        return _json_response(request, {"status": "ok"})

    with BaselithClient(BASE, api_key="k", transport=httpx.MockTransport(handler)) as c:
        c.submit_feedback(
            query="q", answer="a", feedback="negative", idempotency_key="fixed-key"
        )
    assert captured["idem"] == "fixed-key"


# === Error mapping ===
@pytest.mark.parametrize(
    "status,exc",
    [
        (401, AuthenticationError),
        (403, PermissionError_),
        (404, NotFoundError),
        (500, ServerError),
    ],
)
def test_error_status_maps_to_exception(status, exc):
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            {
                "error": {
                    "code": "x",
                    "message": "boom",
                    "type": "T",
                    "request_id": "r1",
                }
            },
            status=status,
            headers={"X-Request-ID": "r1"},
        )

    with BaselithClient(
        BASE, api_key="k", max_retries=0, transport=httpx.MockTransport(handler)
    ) as c:
        with pytest.raises(exc) as ei:
            c.chat("q")
    assert ei.value.status_code == status
    assert ei.value.request_id == "r1"
    assert ei.value.message == "boom"


def test_problem_json_error_document_is_parsed() -> None:
    """RFC 9457 problem+json (current server format) maps code/detail/request_id."""
    from baselith_sdk.errors import NotFoundError, error_from_response

    body = {
        "type": "urn:baselith:error:not_found",
        "title": "Not Found",
        "status": 404,
        "detail": "no such run",
        "instance": "/runs/42",
        "code": "not_found",
        "request_id": "req-9",
    }
    err = error_from_response(404, body)
    assert isinstance(err, NotFoundError)
    assert err.code == "not_found"
    assert err.error_type == "urn:baselith:error:not_found"
    assert err.message == "no such run"
    assert err.request_id == "req-9"

    # title is the fallback when detail is missing
    err2 = error_from_response(
        500, {"title": "Internal Server Error", "code": "internal_error"}
    )
    assert err2.code == "internal_error"
    assert err2.message == "Internal Server Error"


def test_error_envelope_parsing_populates_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            {"error": {"code": "insufficient_scope", "message": "no", "type": "E"}},
            status=403,
        )

    with BaselithClient(
        BASE, api_key="k", max_retries=0, transport=httpx.MockTransport(handler)
    ) as c:
        with pytest.raises(PermissionError_) as ei:
            c.chat("q")
    assert ei.value.code == "insufficient_scope"


# === Retry behaviour ===
def test_retries_on_429_then_succeeds(monkeypatch):
    import baselith_sdk.client as client_mod

    monkeypatch.setattr(client_mod.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return _json_response(request, {"answer": "ok"})

    with BaselithClient(
        BASE, api_key="k", max_retries=2, transport=httpx.MockTransport(handler)
    ) as c:
        resp = c.chat("q")
    assert resp.answer == "ok"
    assert calls["n"] == 2


def test_gives_up_after_max_retries(monkeypatch):
    import baselith_sdk.client as client_mod

    monkeypatch.setattr(client_mod.time, "sleep", lambda *_: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "down"}})

    with BaselithClient(
        BASE, api_key="k", max_retries=1, transport=httpx.MockTransport(handler)
    ) as c:
        with pytest.raises(ServerError):
            c.chat("q")


def test_retry_after_header_honored(monkeypatch):
    import baselith_sdk.client as client_mod

    slept = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: slept.append(s))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        return _json_response(request, {"answer": "ok"})

    with BaselithClient(
        BASE, api_key="k", max_retries=2, transport=httpx.MockTransport(handler)
    ) as c:
        c.chat("q")
    assert slept == [7.0]


def test_connection_error_wrapped():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with BaselithClient(
        BASE, api_key="k", max_retries=0, transport=httpx.MockTransport(handler)
    ) as c:
        with pytest.raises(APIConnectionError):
            c.chat("q")


# === Async ===
@pytest.mark.asyncio
async def test_async_chat():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(request, {"answer": "async-world"})

    async with AsyncBaselithClient(
        BASE, api_key="k", transport=httpx.MockTransport(handler)
    ) as c:
        resp = await c.chat("hello")
    assert resp.answer == "async-world"


@pytest.mark.asyncio
async def test_async_stream():
    def handler(request: httpx.Request) -> httpx.Response:
        body = "data: a\n\ndata: b\n\ndata: c\n\nevent: done\ndata: [DONE]\n\n"
        return httpx.Response(200, text=body)

    async with AsyncBaselithClient(
        BASE, api_key="k", transport=httpx.MockTransport(handler)
    ) as c:
        chunks = [chunk async for chunk in c.chat_stream("q")]
    assert chunks == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_async_stream_tolerates_keepalive_and_multiline():
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            ": keepalive\n\n"
            "data: line1\ndata: line2\n\n"
            ": keepalive\n\n"
            "event: done\ndata: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    async with AsyncBaselithClient(
        BASE, api_key="k", transport=httpx.MockTransport(handler)
    ) as c:
        chunks = [chunk async for chunk in c.chat_stream("q")]
    assert chunks == ["line1\nline2"]


@pytest.mark.asyncio
async def test_async_stream_surfaces_error_event():
    def handler(request: httpx.Request) -> httpx.Response:
        body = "data: partial\n\nevent: error\ndata: stream failed\n\nevent: done\ndata: [DONE]\n\n"
        return httpx.Response(200, text=body)

    async with AsyncBaselithClient(
        BASE, api_key="k", transport=httpx.MockTransport(handler)
    ) as c:
        gen = c.chat_stream("q")
        assert await gen.__anext__() == "partial"
        with pytest.raises(ChatStreamError):
            await gen.__anext__()


@pytest.mark.asyncio
async def test_async_error_maps():
    # 401 is non-retryable and max_retries=0, so no backoff sleep occurs.
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(request, {"error": {"message": "nope"}}, status=401)

    async with AsyncBaselithClient(
        BASE, api_key="k", max_retries=0, transport=httpx.MockTransport(handler)
    ) as c:
        with pytest.raises(AuthenticationError):
            await c.chat("q")
