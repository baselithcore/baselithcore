"""
Redis-backed Cache implementations.

Provides Redis-backed TTL cache using redis.asyncio for non-blocking I/O.
"""

from __future__ import annotations

import hashlib
from core.observability.logging import get_logger
import pickle  # nosec B403
from typing import Generic, Optional, TypeVar

try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None  # type: ignore[assignment,misc]

K = TypeVar("K")
V = TypeVar("V")

logger = get_logger(__name__)


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
        payload = pickle.dumps(key, protocol=4)
        digest = hashlib.sha1(payload, usedforsecurity=False).hexdigest()  # noqa: S324
        return f"{self._prefix}:{digest}"

    async def get(self, key: K) -> Optional[V]:
        """Get a value from Redis cache."""
        redis_key = self._serialize_key(key)
        try:
            data = await self._client.get(redis_key)
            if data is None:
                return None
            return pickle.loads(data)  # nosec B301
        except Exception as e:
            logger.warning(f"Error reading from Redis cache: {e}")
            try:
                await self._client.delete(redis_key)
            except Exception:
                pass  # nosec B110
            return None

    async def set(self, key: K, value: V) -> None:
        """Set a value in Redis cache with TTL."""
        redis_key = self._serialize_key(key)
        payload = pickle.dumps(value, protocol=4)
        await self._client.setex(redis_key, self._ttl, payload)

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
    """Create an async Redis client from a URL."""
    if Redis is None:
        raise RuntimeError("redis package is not installed.")
    return Redis.from_url(url)
