"""
Distributed caching system.

Provides a unified caching interface with multiple backends (memory, Redis).
"""

from __future__ import annotations

import hashlib
import json
from core.observability.logging import get_logger
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TypeVar, Coroutine


logger = get_logger(__name__)

T = TypeVar("T")


class AsyncCacheBackend(ABC):
    """Abstract base class for async cache backends."""

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache with optional TTL (seconds)."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete key from cache. Returns True if existed."""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        ...

    @abstractmethod
    async def clear(self) -> None:
        """Clear all entries from cache."""
        ...


class MemoryCache(AsyncCacheBackend):
    """In-memory cache with TTL support."""

    def __init__(self, max_size: int = 10000) -> None:
        self._cache: dict[str, tuple[Any, float]] = {}
        self._max_size = max_size

    async def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None

        value, expires_at = self._cache[key]
        if expires_at > 0 and time.time() > expires_at:
            del self._cache[key]
            return None

        return value

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        # Evict if over max size
        if len(self._cache) >= self._max_size:
            self._evict_expired()
            if len(self._cache) >= self._max_size:
                # Remove oldest entry
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]

        expires_at = 0 if ttl is None else time.time() + ttl
        self._cache[key] = (value, expires_at)

    async def delete(self, key: str) -> bool:
        existed = key in self._cache
        if existed:
            del self._cache[key]
        return existed

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def clear(self) -> None:
        self._cache.clear()

    def _evict_expired(self) -> None:
        """Remove expired entries."""
        current_time = time.time()
        expired = [
            k for k, (_, exp) in self._cache.items() if exp > 0 and current_time > exp
        ]
        for k in expired:
            del self._cache[k]

    def __len__(self) -> int:
        return len(self._cache)


class RedisCache(AsyncCacheBackend):
    """Redis-backed cache."""

    def __init__(
        self,
        url: Optional[str] = None,
        prefix: str = "baselithcore",
    ) -> None:
        if url is None:
            # Fallback to global config if available
            try:
                from core.config import get_storage_config

                url = get_storage_config().cache_redis_url
            except Exception:
                pass

            self._url = url or "redis://redis:6379/1"
        else:
            self._url = url

        self._prefix = prefix
        self._client: Optional[Any] = None

    async def _get_client(self):
        """Lazy initialize Redis client."""
        if self._client is None:
            try:
                import redis.asyncio as redis

                self._client = redis.from_url(self._url)
            except ImportError:
                raise ImportError(
                    "redis package required for Redis cache (pip install redis)"
                ) from None
        return self._client

    def _key(self, key: str) -> str:
        """Prefix the key."""
        return f"{self._prefix}:{key}"

    async def get(self, key: str) -> Optional[Any]:
        try:
            client = await self._get_client()
            data = await client.get(self._key(key))
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.warning(f"Redis get error: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            data = json.dumps(value, default=str)
            client = await self._get_client()
            if ttl:
                await client.setex(self._key(key), ttl, data)
            else:
                await client.set(self._key(key), data)
        except Exception as e:
            logger.warning(f"Redis set error: {e}")

    async def delete(self, key: str) -> bool:
        try:
            client = await self._get_client()
            return await client.delete(self._key(key)) > 0
        except Exception as e:
            logger.warning(f"Redis delete error: {e}")
            return False

    async def exists(self, key: str) -> bool:
        try:
            client = await self._get_client()
            count = await client.exists(self._key(key))
            return int(count) > 0
        except Exception as e:
            logger.warning(f"Redis exists error: {e}")
            return False

    async def clear(self) -> None:
        """Clear all keys with prefix."""
        try:
            client = await self._get_client()
            keys = await client.keys(f"{self._prefix}:*")
            if keys:
                await client.delete(*keys)
        except Exception as e:
            logger.warning(f"Redis clear error: {e}")


class Cache:
    """
    High-level cache interface with decorator support.

    Usage:
        cache = Cache(backend=MemoryCache())

        @cache.cached(ttl=3600)
        async def expensive_operation(x, y):
            return await compute(x, y)

        # Or manual usage
        await cache.set("key", value, ttl=300)
        value = await cache.get("key")
    """

    def __init__(self, backend: Optional[AsyncCacheBackend] = None) -> None:
        self._backend = backend or MemoryCache()
        self._hits = 0
        self._misses = 0

    @property
    def backend(self) -> AsyncCacheBackend:
        """Get the underlying cache backend."""
        return self._backend

    @property
    def stats(self) -> dict[str, float]:
        """Get cache statistics."""
        total = self._hits + self._misses
        return {
            "hits": float(self._hits),
            "misses": float(self._misses),
            "hit_rate": float(self._hits / total) if total > 0 else 0.0,
        }

    async def get(self, key: str) -> Optional[Any]:
        """
        Retrieve a value from the cache.

        Args:
            key: The cache key to look up.

        Returns:
            Optional[Any]: The cached value if found and not expired, else None.
        """
        value = await self._backend.get(key)
        if value is None:
            self._misses += 1
        else:
            self._hits += 1
        return value

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Store a value in the cache.

        Args:
            key: The unique identifier.
            value: The data to store.
            ttl: Optional time-to-live in seconds.
        """
        await self._backend.set(key, value, ttl)

    async def delete(self, key: str) -> bool:
        """
        Evict a specific key from the cache.

        Args:
            key: The key to delete.

        Returns:
            bool: True if the key existed and was deleted.
        """
        return await self._backend.delete(key)

    async def exists(self, key: str) -> bool:
        """
        Check if a key exists and is valid in the cache.

        Args:
            key: The key to check.

        Returns:
            bool: True if valid entry exists.
        """
        return await self._backend.exists(key)

    async def clear(self) -> None:
        """Flush all entries and reset statistics."""
        await self._backend.clear()
        self._hits = 0
        self._misses = 0

    def cached(
        self,
        ttl: Optional[int] = None,
        key_prefix: str = "",
    ) -> Callable:
        """
        Decorator to cache function results. Function MUST be async.

        Args:
            ttl: Time to live in seconds
            key_prefix: Prefix for cache keys

        Returns:
            Decorator function
        """

        def decorator(
            func: Callable[..., Coroutine[Any, Any, T]],
        ) -> Callable[..., Coroutine[Any, Any, T]]:
            import functools

            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                # Generate cache key from function and arguments
                key_parts = [key_prefix or func.__name__]
                key_parts.extend(str(a) for a in args)
                key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
                cache_key = hashlib.sha256(":".join(key_parts).encode()).hexdigest()

                # Try to get from cache
                cached = await self.get(cache_key)
                if cached is not None:
                    return cached

                # Compute and cache
                result = await func(*args, **kwargs)
                await self.set(cache_key, result, ttl)
                return result

            return wrapper

        return decorator


# Factory function
def create_cache(backend_type: str = "memory", **kwargs) -> Cache:
    """
    Create a cache with the specified backend.

    Args:
        backend_type: "memory" or "redis"
        **kwargs: Backend-specific arguments

    Returns:
        Configured Cache instance
    """
    backend: AsyncCacheBackend
    if backend_type == "redis":
        backend = RedisCache(
            url=kwargs.get("url"),
            prefix=kwargs.get("prefix", "mas"),
        )
    else:
        backend = MemoryCache(max_size=kwargs.get("max_size", 10000))

    return Cache(backend=backend)


# Global cache instance
_cache: Optional[Cache] = None


def get_cache() -> Cache:
    """Get or create global cache instance."""
    global _cache
    if _cache is None:
        _cache = create_cache("memory")
    return _cache


# Global aliases for backward compatibility
cache = get_cache().cached


__all__ = [
    "AsyncCacheBackend",
    "MemoryCache",
    "RedisCache",
    "Cache",
    "create_cache",
    "get_cache",
    "cache",
]
