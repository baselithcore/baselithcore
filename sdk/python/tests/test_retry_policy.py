"""Per-call retry policy, idempotency keys and timeouts of the Python SDK.

Approval decisions, run resumes and delivery replays are non-replayable: the
server may still be executing them after a read timeout or a ``5xx``, so the
client must never re-send them on those — only when the request provably never
left (connect errors) or was refused up front (``429``). SSE streams use their
own read timeout so a quiet, heartbeat-only stream is not cut at 30 s.
"""

import httpx
import pytest
from baselith_sdk import AsyncBaselithClient, BaselithClient, ServerError
from baselith_sdk.errors import APIConnectionError

BASE = "https://api.test"
_RESUME = {"run_id": "r1", "result": "done"}
_DECISION = {"run_id": "r1", "recorded": True, "approved": True}
_DELIVERY = {"id": "d1", "endpoint_id": "e1", "event_id": "ev1", "event_type": "t"}
_REPLAY = {"status": "success", "delivery": _DELIVERY}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import baselith_sdk._base as base_mod

    async def _instant(*_a, **_k):
        return None

    monkeypatch.setattr(base_mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(base_mod.asyncio, "sleep", _instant)


class _Script:
    """Replays ``steps`` in order; each is a Response or an exception to raise."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        return step


def _client(script, **kw):
    kw.setdefault("max_retries", 2)
    return BaselithClient(
        BASE, api_key="k", transport=httpx.MockTransport(script), **kw
    )


def _aclient(script, **kw):
    kw.setdefault("max_retries", 2)
    return AsyncBaselithClient(
        BASE, api_key="k", transport=httpx.MockTransport(script), **kw
    )


_UNSAFE_CALLS = [
    ("resume", lambda c: c.resume_run("r1"), _RESUME),
    ("decide", lambda c: c.decide_approval("r1", True), _DECISION),
    ("replay", lambda c: c.replay_webhook_delivery("d1"), _REPLAY),
]


# === Idempotency-Key ===
@pytest.mark.parametrize("name,call,payload", _UNSAFE_CALLS)
def test_unsafe_calls_send_an_auto_idempotency_key(name, call, payload):
    script = _Script(httpx.Response(200, json=payload))
    with _client(script) as c:
        call(c)
    assert script.requests[0].headers.get("Idempotency-Key")


def test_explicit_idempotency_keys_are_honoured():
    script = _Script(
        httpx.Response(200, json=_RESUME),
        httpx.Response(200, json=_DECISION),
        httpx.Response(200, json=_REPLAY),
    )
    with _client(script) as c:
        c.resume_run("r1", idempotency_key="k-resume")
        c.decide_approval("r1", True, idempotency_key="k-decide")
        c.replay_webhook_delivery("d1", idempotency_key="k-replay")
    keys = [r.headers["Idempotency-Key"] for r in script.requests]
    assert keys == ["k-resume", "k-decide", "k-replay"]


# === No re-send after a timeout or a 5xx ===
@pytest.mark.parametrize("name,call,payload", _UNSAFE_CALLS)
@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_unsafe_calls_not_retried_on_5xx(name, call, payload, status):
    script = _Script(httpx.Response(status, json={}), httpx.Response(200, json=payload))
    with _client(script) as c:
        with pytest.raises(ServerError):
            call(c)
    assert len(script.requests) == 1


@pytest.mark.parametrize("name,call,payload", _UNSAFE_CALLS)
def test_unsafe_calls_not_retried_on_read_timeout(name, call, payload):
    script = _Script(httpx.ReadTimeout("slow"), httpx.Response(200, json=payload))
    with _client(script) as c:
        with pytest.raises(APIConnectionError):
            call(c)
    assert len(script.requests) == 1


def test_unsafe_call_not_retried_on_mid_request_transport_error():
    script = _Script(
        httpx.RemoteProtocolError("reset"), httpx.Response(200, json=_RESUME)
    )
    with _client(script) as c:
        with pytest.raises(APIConnectionError):
            c.resume_run("r1")
    assert len(script.requests) == 1


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("refused"), httpx.ConnectTimeout("syn")]
)
def test_unsafe_call_retried_when_never_sent_with_same_key(exc):
    script = _Script(exc, httpx.Response(200, json=_RESUME))
    with _client(script) as c:
        assert c.resume_run("r1").result == "done"
    assert len(script.requests) == 2
    keys = {r.headers["Idempotency-Key"] for r in script.requests}
    assert len(keys) == 1


def test_unsafe_call_retried_on_429():
    script = _Script(httpx.Response(429, json={}), httpx.Response(200, json=_REPLAY))
    with _client(script) as c:
        c.replay_webhook_delivery("d1")
    assert len(script.requests) == 2


def test_safe_calls_keep_retrying_on_5xx_and_timeouts():
    script = _Script(
        httpx.ReadTimeout("slow"),
        httpx.Response(503, json={}),
        httpx.Response(200, json={"status": "ok"}),
    )
    with _client(script) as c:
        assert c.health().status == "ok"
    assert len(script.requests) == 3


# === Timeouts ===
def test_resume_default_and_per_call_timeout():
    script = _Script(httpx.Response(200, json={**_RESUME, **_DECISION}))
    with _client(script, timeout=30.0) as c:
        c.resume_run("r1")
        c.resume_run("r1", timeout=900.0)
        c.decide_approval("r1", True)
    timeouts = [r.extensions["timeout"]["read"] for r in script.requests]
    assert timeouts == [660.0, 900.0, 30.0]


_SSE = 'event: final\nid: e1\ndata: {"type": "final", "content": "x"}\n\n'
_SSE_HEADERS = {"content-type": "text/event-stream"}


def test_streams_use_the_stream_read_timeout():
    script = _Script(httpx.Response(200, text=_SSE, headers=_SSE_HEADERS))
    with _client(script, timeout=30.0) as c:
        list(c.stream_run_events("r1"))
        list(c.chat_stream("q"))
    for req in script.requests:
        assert req.extensions["timeout"]["read"] == 60.0
        assert req.extensions["timeout"]["connect"] == 30.0


def test_stream_read_timeout_is_configurable():
    script = _Script(httpx.Response(200, text=_SSE, headers=_SSE_HEADERS))
    with _client(script, stream_read_timeout=None) as c:
        list(c.stream_run_events("r1"))
    assert script.requests[0].extensions["timeout"]["read"] is None


def test_plain_requests_keep_the_client_timeout():
    script = _Script(httpx.Response(200, json={"status": "ok"}))
    with _client(script, timeout=12.0) as c:
        c.health()
    assert script.requests[0].extensions["timeout"]["read"] == 12.0


# === Async ===
@pytest.mark.asyncio
async def test_async_resume_not_retried_on_timeout_and_keyed():
    script = _Script(httpx.ReadTimeout("slow"), httpx.Response(200, json=_RESUME))
    async with _aclient(script) as c:
        with pytest.raises(APIConnectionError):
            await c.resume_run("r1")
    assert len(script.requests) == 1
    assert script.requests[0].headers.get("Idempotency-Key")
    assert script.requests[0].extensions["timeout"]["read"] == 660.0


@pytest.mark.asyncio
async def test_async_unsafe_calls_not_retried_on_5xx():
    script = _Script(httpx.Response(503, json={}))
    async with _aclient(script) as c:
        with pytest.raises(ServerError):
            await c.decide_approval("r1", True)
        with pytest.raises(ServerError):
            await c.replay_webhook_delivery("d1", idempotency_key="k")
    assert len(script.requests) == 2


@pytest.mark.asyncio
async def test_async_unsafe_call_retried_on_connect_error():
    script = _Script(httpx.ConnectError("refused"), httpx.Response(200, json=_RESUME))
    async with _aclient(script) as c:
        await c.resume_run("r1", timeout=5.0)
    assert len(script.requests) == 2
    assert script.requests[1].extensions["timeout"]["read"] == 5.0


@pytest.mark.asyncio
async def test_async_streams_use_the_stream_read_timeout():
    script = _Script(httpx.Response(200, text=_SSE, headers=_SSE_HEADERS))
    async with _aclient(script, stream_read_timeout=120.0) as c:
        _ = [e async for e in c.stream_run_events("r1")]
        _ = [t async for t in c.chat_stream("q")]
    assert [r.extensions["timeout"]["read"] for r in script.requests] == [120.0, 120.0]
