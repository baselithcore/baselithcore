"""
Redis-backed Cache implementations.

Provides Redis-backed TTL cache using redis.asyncio for non-blocking I/O.
"""

from __future__ import annotations

import hashlib
import json
from core.observability.logging import get_logger
from threading import Lock
from typing import Any, Generic, Optional, Sequence, TypeVar

try:
    from redis.asyncio import ConnectionPool, Redis
except ImportError:
    ConnectionPool = None  # type: ignore[assignment,misc]
    Redis = None  # type: ignore[assignment,misc]

K = TypeVar("K")
V = TypeVar("V")

logger = get_logger(__name__)
_shared_pools: dict[str, ConnectionPool] = {}
_shared_pools_lock = Lock()


def _json_default(obj: Any) -> Any:
    """Fallback serializer for types not natively supported by json.dumps."""
    if hasattr(obj, "__float__"):
        return float(obj)
    return str(obj)


class RedisTTLCache(Generic[K, V]):
    """
    Redis-backed TTL cache using async client.
    """

    def __init__(
        self,
        client: Redis,
        *,
        prefix: str | None = None,
        default_ttl: float | None = None,
    ) -> None:
        if Redis is None:
            raise RuntimeError("redis package is not installed.")

        from core.config.cache import get_redis_cache_config

        config = get_redis_cache_config()

        self._client = client
        self._prefix = (prefix or config.cache_prefix).rstrip(":")
        self._ttl = max(1, int(default_ttl or config.cache_ttl))

    def _serialize_key(self, key: K) -> str:
        payload = json.dumps(key, sort_keys=True, default=_json_default).encode("utf-8")
        digest = hashlib.sha1(payload, usedforsecurity=False).hexdigest()  # noqa: S324
        return f"{self._prefix}:{digest}"

    def _serialize_value(self, value: V) -> bytes:
        return json.dumps(value, default=_json_default).encode("utf-8")

    def _deserialize_value(self, data: bytes) -> V:
        return json.loads(data)

    async def get(self, key: K) -> Optional[V]:
        """Get a value from Redis cache."""
        redis_key = self._serialize_key(key)
        try:
            data = await self._client.get(redis_key)
            if data is None:
                return None
            return self._deserialize_value(data)
        except Exception as e:
            logger.warning(f"Error reading from Redis cache: {e}")
            try:
                await self._client.delete(redis_key)
            except Exception:
                pass
            return None

    async def set(self, key: K, value: V) -> None:
        """Set a value in Redis cache with TTL."""
        redis_key = self._serialize_key(key)
        payload = self._serialize_value(value)
        await self._client.setex(redis_key, self._ttl, payload)

    async def get_many(self, keys: Sequence[K]) -> list[Optional[V]]:
        """Get multiple values from Redis in a single round-trip."""
        if not keys:
            return []

        redis_keys = [self._serialize_key(key) for key in keys]
        try:
            payloads = await self._client.mget(redis_keys)
        except Exception as e:
            logger.warning(f"Error reading from Redis cache in batch: {e}")
            return [None] * len(redis_keys)

        results: list[Optional[V]] = []
        for redis_key, payload in zip(redis_keys, payloads):
            if payload is None:
                results.append(None)
                continue

            try:
                results.append(self._deserialize_value(payload))
            except Exception as e:
                logger.warning(f"Error deserializing Redis cache value: {e}")
                try:
                    await self._client.delete(redis_key)
                except Exception:
                    pass
                results.append(None)

        return results

    async def set_many(self, items: Sequence[tuple[K, V]]) -> None:
        """Set multiple values in Redis in a single pipeline."""
        if not items:
            return

        pipe = self._client.pipeline(transaction=False)
        for key, value in items:
            pipe.setex(
                self._serialize_key(key), self._ttl, self._serialize_value(value)
            )
        await pipe.execute()

    async def delete(self, key: K) -> None:
        """Delete a value from Redis cache."""
        redis_key = self._serialize_key(key)
        await self._client.delete(redis_key)

    async def clear(self) -> None:
        """Clear all entries with the configured prefix."""
        pattern = f"{self._prefix}:*"
        cursor = 0
        while True:
            cursor, keys = await self._client.scan(
                cursor=cursor, match=pattern, count=500
            )
            if keys:
                await self._client.delete(*keys)
            if cursor == 0:
                break


def create_redis_client(url: str) -> Redis:
    """Create an async Redis client backed by a shared connection pool."""
    if Redis is None or ConnectionPool is None:
        raise RuntimeError("redis package is not installed.")

    with _shared_pools_lock:
        pool = _shared_pools.get(url)
        if pool is None:
            pool = ConnectionPool.from_url(url)
            _shared_pools[url] = pool

    return Redis(connection_pool=pool)


async def close_redis_pools() -> None:
    """Close all shared Redis connection pools."""
    if ConnectionPool is None:
        return

    with _shared_pools_lock:
        pools = list(_shared_pools.values())
        _shared_pools.clear()

    for pool in pools:
        await pool.disconnect()
