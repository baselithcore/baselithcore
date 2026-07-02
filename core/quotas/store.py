"""
Storage backends for per-key usage quotas.

A quota counter is keyed by ``identity:window:period`` (the period embeds the
calendar date, so counters reset naturally when the window rolls over). The
store only needs atomic increment + read; :class:`InMemoryQuotaStore` is the
single-process default and :class:`RedisQuotaStore` shares counters across
workers with a TTL bounding stale keys.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from core.observability.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class QuotaStore(Protocol):
    """Atomic counter store for quota windows."""

    async def get(self, window_key: str) -> int: ...

    async def incr(self, window_key: str, amount: int, ttl_seconds: int) -> int: ...

    async def get_many(self, window_keys: Sequence[str]) -> list[int]: ...

    async def incr_many(self, items: Sequence[tuple[str, int, int]]) -> list[int]: ...


class InMemoryQuotaStore:
    """Process-local counter store. Counters live until the process exits.

    Window keys embed the period date, so a new period uses a fresh key and the
    old one simply lingers (bounded pruning is unnecessary for typical key
    cardinality; use the Redis backend for multi-worker correctness).
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def get(self, window_key: str) -> int:
        return self._counts.get(window_key, 0)

    async def incr(self, window_key: str, amount: int, ttl_seconds: int) -> int:
        self._counts[window_key] = self._counts.get(window_key, 0) + amount
        return self._counts[window_key]

    async def get_many(self, window_keys: Sequence[str]) -> list[int]:
        return [self._counts.get(key, 0) for key in window_keys]

    async def incr_many(self, items: Sequence[tuple[str, int, int]]) -> list[int]:
        return [await self.incr(key, amount, ttl) for key, amount, ttl in items]


class RedisQuotaStore:
    """Redis-backed counter: ``INCRBY`` + ``EXPIRE`` on first write."""

    def __init__(self, redis_client: object, prefix: str = "quota:") -> None:
        self._redis = redis_client
        self._prefix = prefix

    async def get(self, window_key: str) -> int:
        raw = await self._redis.get(self._prefix + window_key)  # type: ignore[attr-defined]
        return int(raw) if raw is not None else 0

    async def incr(self, window_key: str, amount: int, ttl_seconds: int) -> int:
        key = self._prefix + window_key
        new_val = await self._redis.incrby(key, amount)  # type: ignore[attr-defined]
        # Set the TTL only when the counter was just created (value == amount),
        # so the window expiry is anchored to its first request.
        if int(new_val) == amount and ttl_seconds > 0:
            await self._redis.expire(key, ttl_seconds)  # type: ignore[attr-defined]
        return int(new_val)

    async def get_many(self, window_keys: Sequence[str]) -> list[int]:
        """Read all counters in one MGET round trip."""
        if not window_keys:
            return []
        raw = await self._redis.mget(  # type: ignore[attr-defined]
            [self._prefix + key for key in window_keys]
        )
        return [int(value) if value is not None else 0 for value in raw]

    async def incr_many(self, items: Sequence[tuple[str, int, int]]) -> list[int]:
        """Increment all counters in one pipeline round trip.

        TTLs are anchored on first write (same semantics as ``incr``); the
        follow-up EXPIRE pipeline only runs for counters created by this
        call, i.e. at most once per window period.
        """
        if not items:
            return []
        pipe = self._redis.pipeline(transaction=False)  # type: ignore[attr-defined]
        for key, amount, _ in items:
            pipe.incrby(self._prefix + key, amount)
        new_values = [int(value) for value in await pipe.execute()]

        fresh = [
            (self._prefix + key, ttl)
            for (key, amount, ttl), new_value in zip(items, new_values)
            if new_value == amount and ttl > 0
        ]
        if fresh:
            expire_pipe = self._redis.pipeline(transaction=False)  # type: ignore[attr-defined]
            for key, ttl in fresh:
                expire_pipe.expire(key, ttl)
            await expire_pipe.execute()
        return new_values


def build_default_store(backend: str) -> QuotaStore:
    """Construct the configured quota store, falling back to in-memory."""
    if backend == "redis":
        try:
            from core.cache.redis_cache import create_redis_client
            from core.config import get_redis_cache_config

            client = create_redis_client(get_redis_cache_config().url)
            return RedisQuotaStore(client)
        except Exception as exc:
            logger.warning("quota_redis_unavailable_fallback_memory: %s", exc)
    return InMemoryQuotaStore()
