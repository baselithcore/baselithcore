"""A refund gives back what was consumed — and never mints a counter.

``INCRBY -1`` on a key whose window had already expired created a permanent
``-1`` with no TTL (the TTL anchor only fires on a positive first write).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from core.config.quotas import QuotaConfig
from core.quotas.manager import QuotaManager
from core.quotas.store import (
    CHECK_AND_INCR_LUA,
    REFUND_LUA,
    InMemoryQuotaStore,
    RedisQuotaStore,
)

NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=UTC)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def register_script(self, lua: str) -> Any:
        if lua == CHECK_AND_INCR_LUA:

            async def consume(keys: list[str], args: list[int]) -> list[int]:
                out = [1]
                for i, key in enumerate(keys):
                    self.values[key] = self.values.get(key, 0) + args[3 * i]
                    out.append(self.values[key])
                return out

            return consume
        assert lua == REFUND_LUA

        async def refund(keys: list[str], args: list[int]) -> list[int]:
            out = []
            for key, amount in zip(keys, args, strict=True):
                if key not in self.values:
                    out.append(0)
                    continue
                self.values[key] = max(0, self.values[key] - amount)
                out.append(self.values[key])
            return out

        return refund


def _mgr(store: Any) -> QuotaManager:
    config = QuotaConfig(
        QUOTAS_ENABLED=True,
        QUOTA_BACKEND="memory",
        QUOTA_DAILY_REQUESTS=5,
        QUOTA_TENANT_DAILY_REQUESTS=5,
    )
    return QuotaManager(config=config, store=store)


@pytest.mark.asyncio
async def test_memory_refund_never_creates_a_counter() -> None:
    store = InMemoryQuotaStore()
    await _mgr(store).refund_pair("u1", "t1", now=NOW)
    assert store._counts == {}


@pytest.mark.asyncio
async def test_memory_refund_floors_at_zero() -> None:
    store = InMemoryQuotaStore()
    m = _mgr(store)
    await m.check_and_consume_pair("u1", "t1", now=NOW)
    await m.refund_pair("u1", "t1", now=NOW)
    await m.refund_pair("u1", "t1", now=NOW)
    assert all(v == 0 for v in store._counts.values())


@pytest.mark.asyncio
async def test_redis_refund_runs_in_one_script_and_skips_missing_keys() -> None:
    fake = _FakeRedis()
    m = _mgr(RedisQuotaStore(fake))
    await m.refund_pair("u1", "t1", now=NOW)
    assert fake.values == {}  # nothing minted
    await m.check_and_consume_pair("u1", "t1", now=NOW)
    await m.refund_pair("u1", "t1", now=NOW)
    assert all(v == 0 for v in fake.values.values())


@pytest.mark.asyncio
async def test_memory_store_is_hard_capped() -> None:
    store = InMemoryQuotaStore()
    cap = InMemoryQuotaStore.MAX_ENTRIES
    for i in range(cap + 100):
        await store.incr(f"id-{i}:daily:2026-06-17", 1, 60)
    assert len(store._counts) <= cap
    assert f"id-{cap + 99}:daily:2026-06-17" in store._counts
