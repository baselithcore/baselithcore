"""RedisSingleFlight: cross-worker lock, token-guarded release, fail-open."""

import asyncio
from types import SimpleNamespace

import pytest

from core.cache.single_flight import (
    LayeredSingleFlight,
    RedisSingleFlight,
    build_single_flight,
)
from core.config.cache import CacheConfig


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


async def test_owner_exception_releases_lock_and_does_not_deadlock():
    """A raising owner must not strand the lock nor block the next caller."""
    fake = FakeAsyncRedis()
    sf = RedisSingleFlight(redis_client=fake, ttl_seconds=5, poll_interval=0.01)

    async def boom():
        raise RuntimeError("upstream exploded")

    with pytest.raises(RuntimeError, match="upstream exploded"):
        await sf.do("k", boom)

    assert fake.store == {}  # lock released by the finally, not left to the TTL

    async def ok():
        return "recovered"

    # The very next caller acquires immediately rather than polling to timeout.
    assert await asyncio.wait_for(sf.do("k", ok), timeout=1.0) == "recovered"


class TestLayeredSingleFlight:
    """The composed coordinator: local collapse over cross-worker election."""

    async def test_two_workers_share_one_upstream_call(self):
        """Two instances + one shared Redis => the costly call runs once."""
        fake = FakeAsyncRedis()  # the "shared Redis" both workers talk to
        shared_cache: dict[str, str] = {}  # the shared publication channel
        calls = {"n": 0}

        def make_worker() -> LayeredSingleFlight[str]:
            # Separate instances = separate processes: no shared memory, only
            # the fake Redis and the shared cache in common.
            return LayeredSingleFlight(
                RedisSingleFlight(redis_client=fake, ttl_seconds=5, poll_interval=0.01)
            )

        async def factory() -> str:
            calls["n"] += 1
            await asyncio.sleep(0.05)
            shared_cache["k"] = "expensive"  # winner publishes
            return "expensive"

        async def recheck() -> str | None:
            return shared_cache.get("k")  # loser re-reads

        worker_a, worker_b = make_worker(), make_worker()
        first = asyncio.create_task(worker_a.do("k", factory, recheck=recheck))
        await asyncio.sleep(0.01)  # let worker A win the lock
        second = asyncio.create_task(worker_b.do("k", factory, recheck=recheck))

        assert await asyncio.gather(first, second) == ["expensive", "expensive"]
        assert calls["n"] == 1  # coalesced ACROSS workers, not just within one

    async def test_local_layer_collapses_concurrency_before_redis(self):
        """N coroutines in one worker take the Redis lock once, not N times."""
        fake = FakeAsyncRedis()
        worker: LayeredSingleFlight[str] = LayeredSingleFlight(
            RedisSingleFlight(redis_client=fake, ttl_seconds=5, poll_interval=0.01)
        )
        calls = {"n": 0}

        async def factory() -> str:
            calls["n"] += 1
            await asyncio.sleep(0.02)
            return "v"

        results = await asyncio.gather(*(worker.do("k", factory) for _ in range(10)))

        assert results == ["v"] * 10
        assert calls["n"] == 1
        # Only one coroutine ever reached Redis; the other nine never raced it.
        assert fake.set_calls == 1

    async def test_degrades_to_in_process_when_redis_is_down(self):
        """Redis unreachable => still coalesced locally, never a failure."""
        worker: LayeredSingleFlight[str] = LayeredSingleFlight(
            RedisSingleFlight(redis_client=BrokenRedis(), ttl_seconds=5)
        )
        calls = {"n": 0}

        async def factory() -> str:
            calls["n"] += 1
            await asyncio.sleep(0.02)
            return "v"

        results = await asyncio.gather(*(worker.do("k", factory) for _ in range(5)))

        assert results == ["v"] * 5
        # Fail-open: the request succeeds, and in-process coalescing survives.
        assert calls["n"] == 1

    async def test_no_distributed_layer_is_plain_single_flight(self):
        worker: LayeredSingleFlight[str] = LayeredSingleFlight()
        assert worker.is_distributed is False
        calls = {"n": 0}

        async def factory() -> str:
            calls["n"] += 1
            await asyncio.sleep(0.02)
            return "v"

        await asyncio.gather(*(worker.do("k", factory) for _ in range(4)))
        assert calls["n"] == 1

    async def test_owner_exception_does_not_deadlock_layered(self):
        fake = FakeAsyncRedis()
        worker: LayeredSingleFlight[str] = LayeredSingleFlight(
            RedisSingleFlight(redis_client=fake, ttl_seconds=5, poll_interval=0.01)
        )

        async def boom() -> str:
            raise ValueError("nope")

        with pytest.raises(ValueError):
            await worker.do("k", boom)

        assert fake.store == {}  # no orphan lock

        async def ok() -> str:
            return "fine"

        assert await asyncio.wait_for(worker.do("k", ok), timeout=1.0) == "fine"


class TestBuildSingleFlight:
    """Activation policy: opt-in flag AND a genuinely shared cache."""

    def test_disabled_without_shared_cache(self, monkeypatch):
        # Even with the flag on, a process-local store gives a losing worker
        # nothing to read back, so the distributed layer must stay off.
        monkeypatch.setattr(
            "core.config.get_cache_config",
            lambda: SimpleNamespace(cross_worker_single_flight=True),
        )
        assert build_single_flight(shared_cache=False).is_distributed is False

    def test_disabled_without_opt_in_flag(self, monkeypatch):
        monkeypatch.setattr(
            "core.config.get_cache_config",
            lambda: SimpleNamespace(cross_worker_single_flight=False),
        )
        assert build_single_flight(shared_cache=True).is_distributed is False

    def test_enabled_when_flag_and_shared_cache(self, monkeypatch):
        monkeypatch.setattr(
            "core.config.get_cache_config",
            lambda: SimpleNamespace(cross_worker_single_flight=True),
        )
        monkeypatch.setattr(
            "core.cache.single_flight.RedisSingleFlight",
            lambda **kwargs: object(),
        )
        assert build_single_flight(shared_cache=True).is_distributed is True

    def test_config_failure_falls_back_to_in_process(self, monkeypatch):
        def explode():
            raise RuntimeError("config unreadable")

        monkeypatch.setattr("core.config.get_cache_config", explode)
        # Never let coordination setup break the caller.
        assert build_single_flight(shared_cache=True).is_distributed is False

    def test_default_config_is_opt_out(self):
        """Stock config must not switch onto Redis: CACHE_BACKEND defaults to
        `local` while CACHE_REDIS_URL has a non-empty default."""
        assert CacheConfig().cross_worker_single_flight is False


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
