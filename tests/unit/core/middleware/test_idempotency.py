"""Tests for the Idempotency-Key middleware."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from core.middleware.idempotency import IdempotencyMiddleware


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
    r1 = client.post("/count", headers={"Idempotency-Key": "k1"})
    r2 = client.post("/count", headers={"Idempotency-Key": "k1"})

    assert r1.json() == {"n": 1}
    assert r2.json() == {"n": 1}  # replayed, not re-executed
    assert state["count"] == 1
    assert r1.headers.get("idempotency-replayed") is None
    assert r2.headers.get("idempotency-replayed") == "true"


def test_no_key_executes_every_time():
    client, state = _build(FakeRedis())
    assert client.post("/count").json() == {"n": 1}
    assert client.post("/count").json() == {"n": 2}


def test_distinct_keys_are_independent():
    client, state = _build(FakeRedis())
    assert client.post("/count", headers={"Idempotency-Key": "a"}).json() == {"n": 1}
    assert client.post("/count", headers={"Idempotency-Key": "b"}).json() == {"n": 2}


def test_streaming_response_is_not_cached():
    client, state = _build(FakeRedis())
    r1 = client.post("/stream", headers={"Idempotency-Key": "s1"})
    r2 = client.post("/stream", headers={"Idempotency-Key": "s1"})
    assert r1.content == b"ab"
    assert r2.content == b"ab"
    # Streaming passes through both times → handler ran twice, no replay.
    assert state["count"] == 2
    assert r2.headers.get("idempotency-replayed") is None


def test_server_error_is_not_cached():
    client, state = _build(FakeRedis())
    r1 = client.post("/fail", headers={"Idempotency-Key": "f1"})
    r2 = client.post("/fail", headers={"Idempotency-Key": "f1"})
    assert r1.status_code == 503
    assert r2.status_code == 503
    # 5xx must not be replayed — each retry re-executes.
    assert state["count"] == 2


def test_in_flight_lock_returns_409():
    fake = FakeRedis()
    client, state = _build(fake)
    # Pre-seed the in-flight lock for this exact request to simulate a
    # concurrent duplicate still running.
    scope = {"type": "http", "method": "POST", "path": "/count", "headers": []}
    with patch("core.middleware.idempotency.create_redis_client", return_value=fake):
        mw = IdempotencyMiddleware(FastAPI())
    lock_key = mw._storage_key(scope, "dup") + ":lock"
    fake.store[lock_key] = "1"

    r = client.post("/count", headers={"Idempotency-Key": "dup"})
    assert r.status_code == 409
    assert state["count"] == 0  # handler never ran


def test_oversized_key_rejected():
    client, state = _build(FakeRedis())
    r = client.post("/count", headers={"Idempotency-Key": "x" * 300})
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
