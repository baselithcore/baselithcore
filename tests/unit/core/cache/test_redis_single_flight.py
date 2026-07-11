"""RedisSingleFlight: cross-worker lock, token-guarded release, fail-open."""

import asyncio

import pytest

from core.cache.single_flight import RedisSingleFlight


class FakeAsyncRedis:
    """Minimal async-redis double: SET NX EX, EXISTS, token-guarded EVAL."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.set_calls = 0

    async def set(self, name, value, nx=False, ex=None):
        self.set_calls += 1
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    async def exists(self, name):
        return 1 if name in self.store else 0

    async def eval(self, script, numkeys, name, token):
        # NOTE: this is the redis-py EVAL *command* (server-side Lua), not
        # Python's builtin eval — the double just mirrors the client API and
        # implements the token-guarded delete semantics of the release script.
        if self.store.get(name) == token:
            del self.store[name]
            return 1
        return 0


class BrokenRedis:
    async def set(self, *a, **k):
        raise ConnectionError("redis down")


async def test_owner_executes_and_releases_lock():
    fake = FakeAsyncRedis()
    sf = RedisSingleFlight(redis_client=fake, ttl_seconds=5)

    async def factory():
        return "computed"

    assert await sf.do("k", factory) == "computed"
    assert fake.store == {}  # lock released after execution


async def test_waiter_resolves_via_recheck():
    fake = FakeAsyncRedis()
    sf = RedisSingleFlight(redis_client=fake, ttl_seconds=5, poll_interval=0.01)
    cache: dict[str, str] = {}
    factory_calls = {"n": 0}

    async def owner_factory():
        factory_calls["n"] += 1
        await asyncio.sleep(0.05)  # hold the lock while the waiter polls
        cache["k"] = "owner-value"
        return "owner-value"

    async def waiter_factory():
        factory_calls["n"] += 1
        return "waiter-recomputed"

    async def recheck():
        return cache.get("k")

    owner = asyncio.create_task(sf.do("k", owner_factory))
    await asyncio.sleep(0.01)  # let the owner acquire first
    waiter = asyncio.create_task(sf.do("k", waiter_factory, recheck=recheck))

    results = await asyncio.gather(owner, waiter)
    assert results == ["owner-value", "owner-value"]
    assert factory_calls["n"] == 1  # single upstream call across "workers"


async def test_waiter_without_recheck_recomputes_after_owner():
    fake = FakeAsyncRedis()
    sf = RedisSingleFlight(redis_client=fake, ttl_seconds=5, poll_interval=0.01)

    async def slow_owner():
        await asyncio.sleep(0.03)
        return "a"

    async def waiter_factory():
        return "b"

    owner = asyncio.create_task(sf.do("k", slow_owner))
    await asyncio.sleep(0.005)
    waiter = asyncio.create_task(sf.do("k", waiter_factory))
    assert await asyncio.gather(owner, waiter) == ["a", "b"]


async def test_release_is_token_guarded():
    fake = FakeAsyncRedis()
    # Simulate another worker's lock already present under a different token.
    fake.store["baselithcore:singleflight:k"] = "someone-elses-token"
    assert await fake.eval("", 1, "baselithcore:singleflight:k", "wrong") == 0
    assert "baselithcore:singleflight:k" in fake.store  # not clobbered


async def test_redis_down_fails_open():
    sf = RedisSingleFlight(redis_client=BrokenRedis(), ttl_seconds=5)

    async def factory():
        return "still-works"

    assert await sf.do("k", factory) == "still-works"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
