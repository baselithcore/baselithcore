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
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Hello world")

    with BaselithClient(BASE, api_key="k", transport=httpx.MockTransport(handler)) as c:
        chunks = "".join(c.chat_stream("q"))
    assert chunks == "Hello world"


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
        return httpx.Response(200, text="a-b-c")

    async with AsyncBaselithClient(
        BASE, api_key="k", transport=httpx.MockTransport(handler)
    ) as c:
        chunks = [chunk async for chunk in c.chat_stream("q")]
    assert "".join(chunks) == "a-b-c"


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
