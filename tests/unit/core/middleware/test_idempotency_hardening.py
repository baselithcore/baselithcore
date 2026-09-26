"""Idempotency hardening: who may store, what is stored, and what is replayed.

Regressions covered:

* any ``Authorization``/``X-API-Key`` value — valid or not — got its own
  bucket, so an unauthenticated client could fill Redis with junk headers;
* ``404``/``405`` (no route ran) were cached, and the raw path was embedded in
  the Redis key;
* a retry reusing a key with a *different* body was answered with the first
  body's response instead of ``422``;
* the in-flight lock (TTL capped at 300 s) could expire under a long handler;
* a gzip-encoded stored body was replayed to a retry that does not accept gzip.
"""

from __future__ import annotations

import asyncio
import gzip
from typing import Any
from unittest.mock import patch

import orjson
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from core.middleware._idempotency_store import keep_lock_alive
from core.middleware.idempotency import IdempotencyMiddleware

pytestmark = pytest.mark.usefixtures("idem_verified_credentials")

_CRED = {"Authorization": "Bearer client-a-token"}


class _Redis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.expires: list[tuple[str, int]] = []

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, nx: bool = False, ex: Any = None) -> Any:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.store.pop(key, None)
        return 1

    async def expire(self, key: str, ttl: int) -> bool:
        self.expires.append((key, ttl))
        return key in self.store


def _stored(fake: _Redis) -> dict[str, Any]:
    return {k: v for k, v in fake.store.items() if not k.endswith(":lock")}


def _app() -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    state = {"count": 0}

    @app.post("/echo")
    async def _echo(request: Request) -> dict[str, Any]:
        state["count"] += 1
        return {"n": state["count"], "body": (await request.body()).decode()}

    @app.post("/nobody")
    def _nobody() -> dict[str, int]:
        state["count"] += 1
        return {"n": state["count"]}

    @app.get("/read-only")
    def _read_only() -> dict[str, bool]:
        return {"ok": True}

    return app, state


def _mw(app: Any, fake: _Redis, **kwargs: Any) -> IdempotencyMiddleware:
    with patch("core.middleware.idempotency.create_redis_client", return_value=fake):
        return IdempotencyMiddleware(app, **kwargs)


def _client(fake: _Redis) -> tuple[TestClient, dict[str, int]]:
    app, state = _app()
    return TestClient(_mw(app, fake), raise_server_exceptions=False), state


def test_unverifiable_credential_gets_no_bucket() -> None:
    fake = _Redis()
    client, state = _client(fake)
    headers = {"Idempotency-Key": "k", "Authorization": "Bearer invalid-junk"}
    client.post("/echo", headers=headers, content=b"x")
    r2 = client.post("/echo", headers=headers, content=b"x")
    assert r2.headers.get("idempotency-replayed") is None
    assert state["count"] == 2
    assert fake.store == {}


def test_unsupported_scheme_gets_no_bucket() -> None:
    fake = _Redis()
    client, state = _client(fake)
    headers = {"Idempotency-Key": "k", "Authorization": "Basic invalid"}
    client.post("/nobody", headers=headers)
    assert fake.store == {}


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [("post", "/no-such-route", 404), ("post", "/read-only", 405)],
)
def test_unrouted_responses_are_never_stored(
    method: str, path: str, status: int
) -> None:
    fake = _Redis()
    client, _ = _client(fake)
    for i in range(3):
        r = getattr(client, method)(path, headers={"Idempotency-Key": f"k{i}", **_CRED})
        assert r.status_code == status
    assert fake.store == {}  # nothing stored, every lock released


def test_raw_path_is_not_embedded_in_the_storage_key() -> None:
    app, _ = _app()
    mw = _mw(app, _Redis())
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/some/very/distinctive/path",
        "headers": [(b"authorization", b"Bearer client-a-token")],
    }
    key = mw._storage_key(scope, "k")
    assert key is not None
    assert "distinctive" not in key
    assert key != mw._storage_key({**scope, "path": "/other"}, "k")


def test_same_key_different_body_is_422() -> None:
    fake = _Redis()
    client, state = _client(fake)
    headers = {"Idempotency-Key": "pay-1", **_CRED}
    r1 = client.post("/echo", headers=headers, content=b'{"amount": 10}')
    r2 = client.post("/echo", headers=headers, content=b'{"amount": 99}')
    r3 = client.post("/echo", headers=headers, content=b'{"amount": 10}')
    assert r1.status_code == 200
    assert r2.status_code == 422
    assert r3.status_code == 200
    assert r3.headers.get("idempotency-replayed") == "true"
    assert r3.json() == r1.json()
    assert state["count"] == 1
    entry = orjson.loads(next(iter(_stored(fake).values())))
    assert len(entry["body_sha256"]) == 64


def test_handler_that_ignores_its_body_still_replays() -> None:
    """No fingerprint is stored when the handler never read the body — its
    result cannot depend on it — so a retry replays as before."""
    fake = _Redis()
    client, state = _client(fake)
    headers = {"Idempotency-Key": "k", **_CRED}
    client.post("/nobody", headers=headers, content=b"a")
    r2 = client.post("/nobody", headers=headers, content=b"b")
    assert r2.headers.get("idempotency-replayed") == "true"
    assert state["count"] == 1


@pytest.mark.asyncio
async def test_lock_is_refreshed_while_the_handler_runs() -> None:
    fake = _Redis()
    fake.store["lk"] = "1"
    task = asyncio.create_task(keep_lock_alive(fake, "lk", 1))
    await asyncio.sleep(0.8)
    task.cancel()
    assert fake.expires[:2] == [("lk", 1), ("lk", 1)]


def test_long_handler_keeps_its_lock_alive() -> None:
    fake = _Redis()
    app = FastAPI()

    @app.post("/slow")
    async def _slow() -> dict[str, bool]:
        await asyncio.sleep(0.45)
        return {"ok": True}

    mw = _mw(app, fake, ttl_seconds=1)
    TestClient(mw).post("/slow", headers={"Idempotency-Key": "s", **_CRED})
    assert any(key.endswith(":lock") for key, _ in fake.expires)


async def _raw_post(mw: Any, path: str, headers: dict[str, str]) -> list[dict]:
    sent: list[dict] = []
    delivered = False

    async def receive() -> dict:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    await mw(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_gzip_entry_is_decompressed_for_a_retry_without_gzip() -> None:
    fake = _Redis()
    app = FastAPI()
    payload = {"blob": "x" * 2000}

    @app.post("/big")
    def _big() -> dict[str, str]:
        return payload

    # Compression runs inside idempotency in the real stack.
    mw = _mw(GZipMiddleware(app, minimum_size=100), fake)
    base = {"Idempotency-Key": "g", **_CRED}

    first = await _raw_post(mw, "/big", {**base, "Accept-Encoding": "gzip"})
    first_headers = dict(first[0]["headers"])
    assert first_headers[b"content-encoding"] == b"gzip"

    again = await _raw_post(mw, "/big", {**base, "Accept-Encoding": "gzip"})
    assert dict(again[0]["headers"])[b"content-encoding"] == b"gzip"
    assert orjson.loads(gzip.decompress(again[1]["body"])) == payload

    plain = await _raw_post(mw, "/big", {**base, "Accept-Encoding": "identity"})
    plain_headers = dict(plain[0]["headers"])
    assert plain_headers[b"idempotency-replayed"] == b"true"
    assert b"content-encoding" not in plain_headers
    assert orjson.loads(plain[1]["body"]) == payload
    assert int(plain_headers[b"content-length"]) == len(plain[1]["body"])
