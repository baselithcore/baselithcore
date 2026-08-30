"""Tests for the Idempotency-Key middleware."""

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from core.middleware.idempotency import IdempotencyMiddleware

# Idempotency is credential-scoped and inert for credential-less callers, so
# every test exercising replay must present a credential — which is also the
# real use case (an authenticated client retrying its own request).
_CRED = {"Authorization": "Bearer client-a-token"}


def _keyed(key: str, **extra: str) -> dict[str, str]:
    """Headers for an idempotent request from the default test credential."""
    return {"Idempotency-Key": key, **_CRED, **extra}


class FakeRedis:
    """Minimal in-memory async stand-in for the Redis client used here."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    def pipeline(self, transaction=False):
        return _FakePipeline(self)


class _FakePipeline:
    """Queues set/delete and applies them on execute() — mirrors redis.asyncio."""

    def __init__(self, redis: "FakeRedis"):
        self._redis = redis
        self._ops: list = []

    def set(self, key, value, nx=False, ex=None):
        self._ops.append(("set", key, value, nx, ex))
        return self

    def delete(self, key):
        self._ops.append(("delete", key))
        return self

    async def execute(self):
        results = []
        for op in self._ops:
            if op[0] == "set":
                results.append(await self._redis.set(op[1], op[2], nx=op[3], ex=op[4]))
            else:
                results.append(await self._redis.delete(op[1]))
        self._ops.clear()
        return results


def _build(fake):
    app = FastAPI()
    state = {"count": 0}

    @app.post("/count")
    def _count():
        state["count"] += 1
        return {"n": state["count"]}

    @app.post("/fail")
    def _fail():
        state["count"] += 1
        raise HTTPException(status_code=503, detail="try later")

    @app.post("/throttled")
    def _throttled():
        state["count"] += 1
        raise HTTPException(status_code=429, detail="slow down")

    @app.post("/badrequest")
    def _badrequest():
        state["count"] += 1
        raise HTTPException(status_code=400, detail="nope")

    @app.post("/stream")
    def _stream():
        state["count"] += 1

        async def gen():
            yield b"a"
            yield b"b"

        return StreamingResponse(gen(), media_type="text/event-stream")

    with patch("core.middleware.idempotency.create_redis_client", return_value=fake):
        mw = IdempotencyMiddleware(app)
    return TestClient(mw, raise_server_exceptions=False), state


def test_same_key_replays_and_runs_once():
    client, state = _build(FakeRedis())
    r1 = client.post("/count", headers=_keyed("k1"))
    r2 = client.post("/count", headers=_keyed("k1"))

    assert r1.json() == {"n": 1}
    assert r2.json() == {"n": 1}  # replayed, not re-executed
    assert state["count"] == 1
    assert r1.headers.get("idempotency-replayed") is None
    assert r2.headers.get("idempotency-replayed") == "true"


def test_no_key_executes_every_time():
    client, state = _build(FakeRedis())
    assert client.post("/count", headers=_CRED).json() == {"n": 1}
    assert client.post("/count", headers=_CRED).json() == {"n": 2}


def test_distinct_keys_are_independent():
    client, state = _build(FakeRedis())
    assert client.post("/count", headers=_keyed("a")).json() == {"n": 1}
    assert client.post("/count", headers=_keyed("b")).json() == {"n": 2}


def test_streaming_response_is_not_cached():
    client, state = _build(FakeRedis())
    r1 = client.post("/stream", headers=_keyed("s1"))
    r2 = client.post("/stream", headers=_keyed("s1"))
    assert r1.content == b"ab"
    assert r2.content == b"ab"
    # Streaming passes through both times → handler ran twice, no replay.
    assert state["count"] == 2
    assert r2.headers.get("idempotency-replayed") is None


def test_server_error_is_not_cached():
    client, state = _build(FakeRedis())
    r1 = client.post("/fail", headers=_keyed("f1"))
    r2 = client.post("/fail", headers=_keyed("f1"))
    assert r1.status_code == 503
    assert r2.status_code == 503
    # 5xx must not be replayed — each retry re-executes.
    assert state["count"] == 2


def test_retryable_4xx_is_not_cached():
    """A 429 (throttling) must not be frozen under the key: a corrected retry
    re-executes and the lock is released rather than replaying the failure."""
    client, state = _build(FakeRedis())
    r1 = client.post("/throttled", headers=_keyed("t1"))
    r2 = client.post("/throttled", headers=_keyed("t1"))
    assert r1.status_code == 429
    assert r2.status_code == 429
    assert state["count"] == 2  # re-executed, not replayed from cache
    assert r2.headers.get("idempotency-replayed") is None


def test_deterministic_4xx_is_still_cached():
    """A deterministic client error (400) is replayed like any success —
    retrying the identical request must yield the identical error."""
    client, state = _build(FakeRedis())
    r1 = client.post("/badrequest", headers=_keyed("b1"))
    r2 = client.post("/badrequest", headers=_keyed("b1"))
    assert r1.status_code == 400
    assert r2.status_code == 400
    # Cached: the handler runs once, the second call replays.
    assert state["count"] == 1
    assert r2.headers.get("idempotency-replayed") == "true"


def test_in_flight_lock_returns_409():
    fake = FakeRedis()
    client, state = _build(fake)
    # Pre-seed the in-flight lock for this exact request to simulate a
    # concurrent duplicate still running.
    # The scope must carry the same credential header as the request below:
    # the storage key is credential-scoped, so a header-less scope would
    # compute a different key (in fact none at all).
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/count",
        "headers": [(b"authorization", _CRED["Authorization"].encode())],
    }
    with patch("core.middleware.idempotency.create_redis_client", return_value=fake):
        mw = IdempotencyMiddleware(FastAPI())
    storage_key = mw._storage_key(scope, "dup")
    assert storage_key is not None
    fake.store[storage_key + ":lock"] = "1"

    r = client.post("/count", headers=_keyed("dup"))
    assert r.status_code == 409
    assert state["count"] == 0  # handler never ran


def test_oversized_key_rejected():
    client, state = _build(FakeRedis())
    r = client.post("/count", headers=_keyed("x" * 300))
    assert r.status_code == 400
    assert state["count"] == 0


@pytest.mark.parametrize("method", ["get"])
def test_non_mutating_methods_pass_through(method):
    fake = FakeRedis()
    app = FastAPI()

    @app.get("/read")
    def _read():
        return {"ok": True}

    with patch("core.middleware.idempotency.create_redis_client", return_value=fake):
        mw = IdempotencyMiddleware(app)
    client = TestClient(mw)
    r = client.get("/read", headers={"Idempotency-Key": "k"})
    assert r.json() == {"ok": True}
    # No idempotency bookkeeping for safe methods.
    assert fake.store == {}


def test_replay_is_scoped_per_credential():
    """A stored response must never replay to a caller with a different
    (or missing) credential: the middleware runs before route auth, so the
    raw Authorization/X-API-Key header is part of the storage key."""
    client, state = _build(FakeRedis())
    r1 = client.post(
        "/count",
        headers={"Idempotency-Key": "k1", "Authorization": "Bearer alice-token"},
    )
    # Same key, different credential: executes fresh, no cross-user replay.
    r2 = client.post(
        "/count",
        headers={"Idempotency-Key": "k1", "Authorization": "Bearer bob-token"},
    )
    # Same key, no credential at all: also isolated from both.
    r3 = client.post("/count", headers={"Idempotency-Key": "k1"})

    assert r1.json() == {"n": 1}
    assert r2.json() == {"n": 2}
    assert r3.json() == {"n": 3}
    assert state["count"] == 3

    # Same credential + same key still replays.
    r4 = client.post(
        "/count",
        headers={"Idempotency-Key": "k1", "Authorization": "Bearer alice-token"},
    )
    assert r4.json() == {"n": 1}
    assert r4.headers.get("idempotency-replayed") == "true"
    assert state["count"] == 3


def test_multi_chunk_response_is_cached_and_replayed():
    """Chunked (non-SSE) responses must still be captured for replay while
    being teed through chunk-by-chunk (no full-body buffering: the start frame
    and every non-final chunk go out as produced; only the final chunk waits
    for the store round-trip)."""
    fake = FakeRedis()
    app = FastAPI()
    state = {"count": 0}

    @app.post("/chunks")
    def _chunks():
        state["count"] += 1

        async def gen():
            yield b'{"n": '
            yield str(state["count"]).encode()
            yield b"}"

        return StreamingResponse(gen(), media_type="application/json")

    with patch("core.middleware.idempotency.create_redis_client", return_value=fake):
        mw = IdempotencyMiddleware(app)
    client = TestClient(mw, raise_server_exceptions=False)

    r1 = client.post("/chunks", headers=_keyed("c1"))
    r2 = client.post("/chunks", headers=_keyed("c1"))
    assert r1.json() == {"n": 1}
    assert r2.json() == {"n": 1}  # replayed
    assert r2.headers.get("idempotency-replayed") == "true"
    assert state["count"] == 1


def test_oversized_body_passes_through_uncached():
    fake = FakeRedis()
    app = FastAPI()
    state = {"count": 0}

    @app.post("/big")
    def _big():
        state["count"] += 1
        return {"blob": "x" * 64}

    with patch("core.middleware.idempotency.create_redis_client", return_value=fake):
        mw = IdempotencyMiddleware(app, max_body_bytes=16)
    client = TestClient(mw, raise_server_exceptions=False)

    r1 = client.post("/big", headers=_keyed("big1"))
    r2 = client.post("/big", headers=_keyed("big1"))
    assert r1.status_code == 200
    assert r1.json() == r2.json()
    assert state["count"] == 2  # never cached, both executed
    # Nothing stored and the in-flight lock was released both times.
    assert all(not k.endswith(":lock") for k in fake.store)


def _anon_client(app, host):
    """A TestClient whose requests appear to come from ``host``."""
    return TestClient(app, client=(host, 12345))


def test_two_anonymous_callers_never_share_a_response():
    """Credential-less callers must not exchange responses.

    They all used to land in one shared ``anon`` bucket, so a caller who
    guessed (or observed) someone else's Idempotency-Key on the same path was
    served that caller's cached response. Now the request simply executes.
    """
    fake = FakeRedis()
    client, state = _build(fake)

    # Alice, unauthenticated, uses key "shared".
    r1 = client.post("/count", headers={"Idempotency-Key": "shared"})
    # Mallory guesses the same key, also unauthenticated.
    r2 = client.post("/count", headers={"Idempotency-Key": "shared"})

    assert r1.json() == {"n": 1}
    # Mallory gets her OWN fresh execution, never Alice's stored body.
    assert r2.json() == {"n": 2}
    assert r2.headers.get("idempotency-replayed") is None
    assert state["count"] == 2
    # Nothing was written for a credential-less caller: no bucket to poison,
    # and no stored body for anyone else to fetch later.
    assert fake.store == {}


def test_anonymous_optin_is_scoped_per_source_address():
    """With the opt-in enabled, anonymous replay is bucketed per peer IP."""
    fake = FakeRedis()
    app = FastAPI()
    state = {"count": 0}

    @app.post("/count")
    def _count():
        state["count"] += 1
        return {"n": state["count"]}

    with patch.dict(
        os.environ, {"BASELITH_IDEMPOTENCY_ALLOW_ANONYMOUS": "true"}, clear=False
    ):
        with patch(
            "core.middleware.idempotency.create_redis_client", return_value=fake
        ):
            mw = IdempotencyMiddleware(app)

    alice = _anon_client(mw, "198.51.100.7")
    mallory = _anon_client(mw, "203.0.113.9")

    r1 = alice.post("/count", headers={"Idempotency-Key": "shared"})
    # Different source address, same key → different bucket, fresh execution.
    r2 = mallory.post("/count", headers={"Idempotency-Key": "shared"})
    # Alice retrying from her own address does get her response replayed.
    r3 = alice.post("/count", headers={"Idempotency-Key": "shared"})

    assert r1.json() == {"n": 1}
    assert r2.json() == {"n": 2}
    assert r3.json() == {"n": 1}
    assert r3.headers.get("idempotency-replayed") == "true"
    assert state["count"] == 2
