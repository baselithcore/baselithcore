"""The in-flight idempotency lock never outlives the request that took it.

Two leaks: a client disconnect (``CancelledError`` is not an ``Exception``)
left the lock for up to ``MAX_LOCK_TTL`` — exactly when the client retries;
and a failed ``_store`` left it too, because the response was "cacheable".
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from core.middleware.idempotency import IdempotencyMiddleware

pytestmark = pytest.mark.usefixtures("idem_verified_credentials")


class _Redis:
    def __init__(self, *, fail_store: bool = False) -> None:
        self.store: dict[str, Any] = {}
        self.fail_store = fail_store

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, nx: bool = False, ex: Any = None) -> Any:
        if self.fail_store and not key.endswith(":lock"):
            raise ConnectionError("redis write failed")
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.store.pop(key, None)
        return 1

    async def expire(self, key: str, ttl: int) -> bool:
        return key in self.store


def _locks(fake: _Redis) -> list[str]:
    return [k for k in fake.store if k.endswith(":lock")]


async def _drive(app: Any, fake: _Redis) -> None:
    with patch("core.middleware.idempotency.create_redis_client", return_value=fake):
        mw = IdempotencyMiddleware(app)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/echo",
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"idempotency-key", b"k1"),
            (b"authorization", b"Bearer client-a"),
        ],
        "route": object(),  # "routed", so the response counts as cacheable
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        pass

    await mw(scope, receive, send)


async def test_client_disconnect_releases_the_lock() -> None:
    fake = _Redis()

    async def cancelled(scope: Any, receive: Any, send: Any) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _drive(cancelled, fake)
    assert _locks(fake) == []


async def test_failed_store_releases_the_lock() -> None:
    fake = _Redis(fail_store=True)

    async def ok(scope: Any, receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    await _drive(ok, fake)
    assert _locks(fake) == []
    assert fake.store == {}  # nothing stored either: the write failed


async def test_handler_that_never_answers_releases_the_lock() -> None:
    fake = _Redis()

    async def silent(scope: Any, receive: Any, send: Any) -> None:
        return None

    await _drive(silent, fake)
    assert _locks(fake) == []
