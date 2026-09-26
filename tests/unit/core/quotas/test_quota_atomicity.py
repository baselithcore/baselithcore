"""Quota check-and-consume is atomic, namespaced, and refundable.

Regressions covered:

* the Redis path used to read every counter (MGET), decide in Python, then
  INCRBY — concurrent requests all passed the read and all incremented, so a
  limit was overshot by up to the concurrency; and a crash between INCRBY and
  EXPIRE left a counter with no TTL;
* identity counters used the bare identity as their key prefix, so a subject
  named ``tenant:acme`` shared counters with tenant ``acme``'s aggregate;
* nothing could give a unit back for a request answered without doing work.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from core.config.quotas import QuotaConfig
from core.quotas import InMemoryQuotaStore, QuotaExceededError, QuotaManager
from core.quotas.store import CHECK_AND_INCR_LUA, RedisQuotaStore

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)


class _FakeRedis:
    """Async Redis double whose reads yield to the loop, like a real network
    client — which is what exposed the read-then-increment race.

    ``register_script`` emulates :data:`CHECK_AND_INCR_LUA` as one indivisible
    step (a Lua script runs atomically on the server)."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> Any:
        await asyncio.sleep(0)
        return self.values.get(key)

    async def mget(self, keys: list[str]) -> list[Any]:
        await asyncio.sleep(0)
        return [self.values.get(k) for k in keys]

    async def incrby(self, key: str, amount: int) -> int:
        await asyncio.sleep(0)
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.ttls[key] = ttl
        return True

    def pipeline(self, transaction: bool = False) -> _FakePipe:
        return _FakePipe(self)

    def register_script(self, lua: str) -> Any:
        assert lua == CHECK_AND_INCR_LUA

        async def run(keys: list[str], args: list[int]) -> list[int]:
            for i, key in enumerate(keys):
                used = self.values.get(key, 0)
                if used + args[3 * i] > args[3 * i + 1]:
                    return [0, i + 1, used]
            out = [1]
            for i, key in enumerate(keys):
                self.values[key] = self.values.get(key, 0) + args[3 * i]
                out.append(self.values[key])
                if args[3 * i + 2] > 0 and key not in self.ttls:
                    self.ttls[key] = args[3 * i + 2]
            return out

        return run


class _FakePipe:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, str, int]] = []

    def incrby(self, key: str, amount: int) -> None:
        self._ops.append(("incrby", key, amount))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", key, ttl))

    async def execute(self) -> list[Any]:
        out: list[Any] = []
        for op, key, value in self._ops:
            if op == "incrby":
                out.append(await self._redis.incrby(key, value))
            else:
                out.append(await self._redis.expire(key, value))
        return out


def _mgr(store: Any, **cfg: Any) -> QuotaManager:
    base: dict[str, Any] = dict(QUOTAS_ENABLED=True, QUOTA_BACKEND="memory")
    base.update(cfg)
    return QuotaManager(config=QuotaConfig(**base), store=store)


async def _admitted(m: QuotaManager, n: int) -> int:
    async def one() -> bool:
        try:
            await m.check_and_consume_pair("u1", "t1", now=NOW)
        except QuotaExceededError:
            return False
        return True

    return sum(await asyncio.gather(*(one() for _ in range(n))))


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_overshoot_on_redis() -> None:
    redis = _FakeRedis()
    m = _mgr(RedisQuotaStore(redis), QUOTA_DAILY_REQUESTS=2)
    assert await _admitted(m, 6) == 2
    assert redis.values["quota:id:u1:daily:20260617"] == 2


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_overshoot_in_memory() -> None:
    m = _mgr(InMemoryQuotaStore(), QUOTA_TENANT_DAILY_REQUESTS=3)
    assert await _admitted(m, 8) == 3


@pytest.mark.asyncio
async def test_every_consumed_redis_counter_gets_a_ttl() -> None:
    redis = _FakeRedis()
    m = _mgr(
        RedisQuotaStore(redis), QUOTA_DAILY_REQUESTS=5, QUOTA_TENANT_DAILY_REQUESTS=5
    )
    await m.check_and_consume_pair("u1", "t1", now=NOW)
    assert set(redis.ttls) == set(redis.values)
    assert all(ttl > 0 for ttl in redis.ttls.values())


@pytest.mark.asyncio
async def test_rejection_on_redis_consumes_nothing() -> None:
    redis = _FakeRedis()
    m = _mgr(
        RedisQuotaStore(redis), QUOTA_DAILY_REQUESTS=5, QUOTA_TENANT_DAILY_REQUESTS=1
    )
    await m.check_and_consume_pair("u1", "t1", now=NOW)
    with pytest.raises(QuotaExceededError) as ei:
        await m.check_and_consume_pair("u1", "t1", now=NOW)
    assert ei.value.identity == "t1" and ei.value.used == 1
    assert redis.values["quota:id:u1:daily:20260617"] == 1


@pytest.mark.asyncio
async def test_identity_cannot_alias_a_tenant_aggregate() -> None:
    """A subject literally named ``tenant:acme`` must not spend tenant acme's
    aggregate budget (nor be blocked by it)."""
    m = _mgr(
        InMemoryQuotaStore(), QUOTA_DAILY_REQUESTS=5, QUOTA_TENANT_DAILY_REQUESTS=1
    )
    await m.check_and_consume("tenant:acme", now=NOW)
    await m.check_and_consume("tenant:acme", now=NOW)
    # Tenant acme's single unit is still available.
    await m.check_and_consume_tenant("acme", now=NOW)
    status = await m.peek_tenant("acme", now=NOW)
    assert status.windows["daily"].used == 1


@pytest.mark.asyncio
async def test_refund_pair_gives_the_units_back() -> None:
    m = _mgr(
        InMemoryQuotaStore(), QUOTA_DAILY_REQUESTS=1, QUOTA_TENANT_DAILY_REQUESTS=1
    )
    await m.check_and_consume_pair("u1", "t1", now=NOW)
    await m.refund_pair("u1", "t1", now=NOW)
    assert (await m.peek("u1", now=NOW)).windows["daily"].used == 0
    assert (await m.peek_tenant("t1", now=NOW)).windows["daily"].used == 0
    # The refunded unit is spendable again.
    await m.check_and_consume_pair("u1", "t1", now=NOW)


@pytest.mark.asyncio
async def test_refund_is_a_noop_when_disabled() -> None:
    store = InMemoryQuotaStore()
    m = _mgr(store, QUOTAS_ENABLED=False, QUOTA_DAILY_REQUESTS=1)
    await m.refund_pair("u1", "t1", now=NOW)
    assert store._counts == {}
