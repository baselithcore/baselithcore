"""
Storage backends for per-key usage quotas.

A quota counter is keyed by ``identity:window:period`` (the period embeds the
calendar date, so counters reset naturally when the window rolls over). The
store only needs atomic increment + read; :class:`InMemoryQuotaStore` is the
single-process default and :class:`RedisQuotaStore` shares counters across
workers with a TTL bounding stale keys.
"""

from __future__ import annotations

from typing import Dict, Protocol, runtime_checkable

from core.observability.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class QuotaStore(Protocol):
    """Atomic counter store for quota windows."""

    async def get(self, window_key: str) -> int: ...

    async def incr(self, window_key: str, amount: int, ttl_seconds: int) -> int: ...


class InMemoryQuotaStore:
    """Process-local counter store. Counters live until the process exits.

    Window keys embed the period date, so a new period uses a fresh key and the
    old one simply lingers (bounded pruning is unnecessary for typical key
    cardinality; use the Redis backend for multi-worker correctness).
    """

    def __init__(self) -> None:
        self._counts: Dict[str, int] = {}

    async def get(self, window_key: str) -> int:
        return self._counts.get(window_key, 0)

    async def incr(self, window_key: str, amount: int, ttl_seconds: int) -> int:
        self._counts[window_key] = self._counts.get(window_key, 0) + amount
        return self._counts[window_key]


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


def build_default_store(backend: str) -> QuotaStore:
    """Construct the configured quota store, falling back to in-memory."""
    if backend == "redis":
        try:
            from core.cache.redis_cache import create_redis_client
            from core.config import get_redis_cache_config

            client = create_redis_client(get_redis_cache_config().url)
            return RedisQuotaStore(client)
        except Exception as exc:  # noqa: BLE001 — degrade rather than fail closed
            logger.warning("quota_redis_unavailable_fallback_memory: %s", exc)
    return InMemoryQuotaStore()
