"""MCP Streamable HTTP session registry — in-memory and Redis-backed.

The registry was process-local, so a session minted on one replica was
unknown to every other one: behind an ordinary round-robin load balancer the
second request of every client got a 404 and the client re-initialized, in a
loop. The Redis-backed store is selected automatically when the deployment
already runs a Redis cache; nothing else changes.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from core.mcp.http_sessions import (
    RedisSessionStore,
    SessionStore,
    build_session_store,
)


class FakeRedis:
    """In-memory stand-in for the async Redis client (str-decoding).

    The expiry arguments are typed and checked exactly as redis-py types them:
    ``ex``/``seconds`` must be an ``int`` (or a ``timedelta``), and redis-py
    raises ``DataError`` *before any I/O* when handed a float. A permissive
    fake hid that — every real call raised, and the store silently degraded to
    its process-local fallback while the tests stayed green.
    """

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.fail = False
        self.calls: list[str] = []
        self.ttls: list[int] = []

    def _guard(self, name: str) -> None:
        self.calls.append(name)
        if self.fail:
            raise ConnectionError("redis is down")

    def _expiry(self, name: str, value: int | None) -> int | None:
        """Reject what redis-py rejects, and record what was accepted."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be datetime.timedelta or int")
        self.ttls.append(value)
        return value

    def _live(self, key: str) -> bool:
        entry = self.store.get(key)
        if entry is None:
            return False
        if entry[1] is not None and entry[1] <= time.monotonic():
            del self.store[key]
            return False
        return True

    async def get(self, key: str) -> str | None:
        self._guard("get")
        return self.store[key][0] if self._live(key) else None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._guard("set")
        self._expiry("ex", ex)
        self.store[key] = (value, time.monotonic() + ex if ex else None)

    async def delete(self, key: str) -> int:
        self._guard("delete")
        return 1 if self.store.pop(key, None) is not None else 0

    async def expire(self, key: str, seconds: int) -> bool:
        self._guard("expire")
        self._expiry("seconds", seconds)
        if key in self.store:
            self.store[key] = (self.store[key][0], time.monotonic() + seconds)
            return True
        if key in self.hashes:
            return True
        return False

    async def hset(self, key: str, field: str, value: str) -> int:
        self._guard("hset")
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hgetall(self, key: str) -> dict[str, str]:
        self._guard("hgetall")
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        self._guard("hdel")
        bucket = self.hashes.get(key, {})
        return sum(1 for field in fields if bucket.pop(field, None) is not None)


class TestInMemoryStore:
    async def test_create_touch_terminate_round_trip(self) -> None:
        store = SessionStore(ttl_seconds=60)

        session = await store.create("owner-1")
        assert session
        assert await store.touch(session, "owner-1") is True
        assert await store.terminate(session, "owner-1") is True
        assert await store.touch(session, "owner-1") is False

    async def test_session_is_bound_to_its_owner(self) -> None:
        store = SessionStore(ttl_seconds=60)
        session = await store.create("owner-1")

        assert await store.touch(session, "owner-2") is False
        assert await store.terminate(session, "owner-2") is False

    async def test_per_owner_cap(self) -> None:
        store = SessionStore(ttl_seconds=60, max_per_owner=2)

        assert await store.create("owner-1")
        assert await store.create("owner-1")
        assert await store.create("owner-1") is None
        # A different identity has its own budget.
        assert await store.create("owner-2")

    async def test_expired_session_is_not_touchable(self) -> None:
        store = SessionStore(ttl_seconds=0.01)
        session = await store.create("owner-1")

        time.sleep(0.02)

        assert await store.touch(session, "owner-1") is False


class TestRedisStore:
    async def test_session_survives_a_second_process(self) -> None:
        """The point of the Redis store: another replica resolves the same id."""
        redis = FakeRedis()
        first = RedisSessionStore(redis, ttl_seconds=60)
        second = RedisSessionStore(redis, ttl_seconds=60)

        session = await first.create("owner-1")

        assert await second.touch(session, "owner-1") is True

    async def test_owner_binding_is_enforced_across_processes(self) -> None:
        redis = FakeRedis()
        first = RedisSessionStore(redis, ttl_seconds=60)
        second = RedisSessionStore(redis, ttl_seconds=60)

        session = await first.create("owner-1")

        assert await second.touch(session, "owner-2") is False

    async def test_terminate_removes_it_everywhere(self) -> None:
        redis = FakeRedis()
        store = RedisSessionStore(redis, ttl_seconds=60)
        session = await store.create("owner-1")

        assert await store.terminate(session, "owner-1") is True
        assert await store.touch(session, "owner-1") is False

    async def test_per_owner_cap_is_shared(self) -> None:
        redis = FakeRedis()
        first = RedisSessionStore(redis, ttl_seconds=60, max_per_owner=2)
        second = RedisSessionStore(redis, ttl_seconds=60, max_per_owner=2)

        assert await first.create("owner-1")
        assert await second.create("owner-1")
        assert await second.create("owner-1") is None

    async def test_anonymous_owner_is_keyed_distinctly(self) -> None:
        redis = FakeRedis()
        store = RedisSessionStore(redis, ttl_seconds=60)
        session = await store.create(None)

        assert await store.touch(session, None) is True
        assert await store.touch(session, "") is False

    async def test_expired_session_key_is_not_touchable(self) -> None:
        redis = FakeRedis()
        store = RedisSessionStore(redis, ttl_seconds=60)
        session = await store.create("owner-1")

        # Age the key past its deadline without waiting for it.
        key = next(k for k in redis.store if k.endswith(session))
        redis.store[key] = (redis.store[key][0], time.monotonic() - 1)

        assert await store.touch(session, "owner-1") is False

    async def test_redis_is_never_handed_a_float_ttl(self) -> None:
        """redis-py raises DataError on a float `ex`, before any I/O — so a
        float TTL meant every call fell back to the process-local store."""
        redis = FakeRedis()
        store = RedisSessionStore(redis, ttl_seconds=3600.0)

        session = await store.create("owner-1")
        await store.touch(session, "owner-1")

        assert redis.ttls, "no expiry was ever set on the session"
        assert all(type(ttl) is int for ttl in redis.ttls)
        # Redis really was used — not the silent fallback.
        assert "set" in redis.calls

    async def test_sub_second_ttl_is_floored_to_one_second(self) -> None:
        """Redis has no sub-second EXPIRE; 0 would mean "never expires"."""
        redis = FakeRedis()
        store = RedisSessionStore(redis, ttl_seconds=0.4)

        await store.create("owner-1")

        assert redis.ttls and min(redis.ttls) == 1

    async def test_redis_failure_degrades_to_the_process_local_store(self) -> None:
        """A Redis blip must not turn the endpoint into a re-initialize loop.

        The fallback is the behaviour this transport had before Redis existed —
        a documented, supported mode, not a widened one: the owner binding is
        still enforced, just only within this process.
        """
        redis = FakeRedis()
        store = RedisSessionStore(redis, ttl_seconds=60)
        redis.fail = True

        session = await store.create("owner-1")

        assert session
        assert await store.touch(session, "owner-1") is True
        assert await store.touch(session, "owner-2") is False


class TestSelection:
    def test_redis_backend_is_selected_when_the_cache_declares_it(
        self, monkeypatch
    ) -> None:
        import core.mcp.http_sessions as sessions_module

        sentinel = object()
        monkeypatch.setattr(
            sessions_module,
            "get_storage_config",
            lambda: SimpleNamespace(cache_backend="redis"),
        )
        monkeypatch.setattr(
            sessions_module, "get_redis_cache_config", lambda: SimpleNamespace(url="r")
        )
        monkeypatch.setattr(
            sessions_module, "create_redis_client", lambda url, **kw: sentinel
        )

        store = build_session_store(
            SimpleNamespace(
                mcp_http_session_ttl_seconds=60, mcp_http_max_sessions_per_client=8
            )
        )

        assert isinstance(store, RedisSessionStore)

    def test_memory_backend_when_no_redis_cache_is_configured(
        self, monkeypatch
    ) -> None:
        import core.mcp.http_sessions as sessions_module

        monkeypatch.setattr(
            sessions_module,
            "get_storage_config",
            lambda: SimpleNamespace(cache_backend="local"),
        )

        store = build_session_store(
            SimpleNamespace(
                mcp_http_session_ttl_seconds=60, mcp_http_max_sessions_per_client=8
            )
        )

        assert type(store) is SessionStore

    def test_unbuildable_redis_client_falls_back_to_memory(self, monkeypatch) -> None:
        import core.mcp.http_sessions as sessions_module

        def _boom(url, **kw):
            raise RuntimeError("redis package is not installed.")

        monkeypatch.setattr(
            sessions_module,
            "get_storage_config",
            lambda: SimpleNamespace(cache_backend="redis"),
        )
        monkeypatch.setattr(
            sessions_module, "get_redis_cache_config", lambda: SimpleNamespace(url="r")
        )
        monkeypatch.setattr(sessions_module, "create_redis_client", _boom)

        store = build_session_store(
            SimpleNamespace(
                mcp_http_session_ttl_seconds=60, mcp_http_max_sessions_per_client=8
            )
        )

        assert type(store) is SessionStore


def test_session_store_is_still_importable_from_http_transport() -> None:
    """Existing importers keep their import path."""
    from core.mcp.http_transport import SessionStore as Reexported

    assert Reexported is SessionStore


@pytest.mark.parametrize("owner", ["owner-1", None])
async def test_unknown_session_id_is_never_accepted(owner) -> None:
    store = SessionStore(ttl_seconds=60)

    assert await store.touch("not-a-session", owner) is False
    assert await store.terminate("not-a-session", owner) is False
