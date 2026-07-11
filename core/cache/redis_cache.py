"""
Redis-backed Cache implementations.

Provides Redis-backed TTL cache using redis.asyncio for non-blocking I/O.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
from collections.abc import Sequence
from threading import Lock
from typing import Any, Generic, TypeVar

import orjson

from core.observability.logging import get_logger

try:
    from redis.asyncio import ConnectionPool, Redis
except ImportError:
    ConnectionPool = None  # type: ignore[assignment,misc]
    Redis = None  # type: ignore[assignment,misc]

K = TypeVar("K")
V = TypeVar("V")

logger = get_logger(__name__)
_shared_pools: dict[tuple[str, bool], ConnectionPool] = {}
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
        early_refresh_beta: float | None = None,
    ) -> None:
        if Redis is None:
            raise RuntimeError("redis package is not installed.")

        from core.config.cache import get_redis_cache_config

        config = get_redis_cache_config()

        self._client = client
        self._prefix = (prefix or config.cache_prefix).rstrip(":")
        self._ttl = max(1, int(default_ttl or config.cache_ttl))
        # XFetch probabilistic early refresh (Vattani et al., "Optimal
        # Probabilistic Cache Stampede Prevention"): as an entry nears expiry
        # one caller probabilistically treats a hit as a miss and recomputes
        # BEFORE the TTL lapses, so the herd never sees a synchronized cold
        # key. beta=0 (default) disables; 1.0 is the canonical setting.
        # Simplified: fixed recompute-window delta (1% of TTL, min 1s)
        # instead of persisting per-entry recompute times.
        if early_refresh_beta is None:
            try:
                early_refresh_beta = max(
                    float(os.getenv("BASELITH_CACHE_XFETCH_BETA", "0")), 0.0
                )
            except ValueError:
                early_refresh_beta = 0.0
        self._xfetch_beta = early_refresh_beta
        self._xfetch_delta_seconds = max(self._ttl * 0.01, 1.0)

    def _jittered_ttl(self) -> int:
        # Spread expiries by up to +10% so entries written in the same burst
        # don't all lapse at once (synchronized mass-miss / thundering herd
        # against the embedder or LLM on window rollover).
        return self._ttl + random.randint(0, max(1, self._ttl // 10))  # nosec B311

    def _serialize_key(self, key: K) -> str:
        # orjson is ~5-10x faster than json here and this runs on every cache
        # operation. OPT_SORT_KEYS keeps the digest deterministic across
        # processes; OPT_NON_STR_KEYS matches json.dumps' int/float key
        # coercion. Note: switching serializer OR hash changes the digest, so a
        # deploy of this change starts with a cold cache (TTL-bounded data).
        payload = orjson.dumps(
            key,
            default=_json_default,
            option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS,
        )
        # SHA-256, not SHA-1: this is a non-cryptographic cache-key digest, but
        # SHA-1 trips security scanners (collision weakness) for no benefit here.
        digest = hashlib.sha256(payload, usedforsecurity=False).hexdigest()
        return f"{self._prefix}:{digest}"

    def _serialize_value(self, value: V) -> bytes:
        # orjson returns bytes directly (~5-10x faster than json on the large
        # payloads stored here) and redis-py accepts bytes. Unlike cache keys,
        # values don't need digest stability, so no sort-keys option is set.
        return orjson.dumps(value, default=_json_default)

    def _deserialize_value(self, data: bytes) -> V:
        return orjson.loads(data)

    def _xfetch_expired(self, pttl_ms: Any) -> bool:
        """Probabilistic early-expiry decision for a live entry.

        True → treat the hit as a miss so THIS caller refreshes while every
        other caller keeps being served the still-valid entry (no herd).
        """
        if not isinstance(pttl_ms, int) or pttl_ms <= 0:
            return False
        remaining = pttl_ms / 1000.0
        # random.random() ∈ [0,1); guard the 0 edge (log(0) = -inf).
        draw = random.random() or 1e-12  # nosec B311 - not cryptographic
        return remaining < -self._xfetch_beta * self._xfetch_delta_seconds * math.log(
            draw
        )

    async def get(self, key: K) -> V | None:
        """Get a value from Redis cache."""
        redis_key = self._serialize_key(key)
        try:
            if self._xfetch_beta > 0:
                pipe = self._client.pipeline()
                pipe.get(redis_key)
                pipe.pttl(redis_key)
                data, pttl_ms = await pipe.execute()
                if data is None:
                    return None
                if self._xfetch_expired(pttl_ms):
                    logger.debug("xfetch_early_refresh key=%s", redis_key)
                    return None  # probabilistic miss: caller recomputes
                return self._deserialize_value(data)
            data = await self._client.get(redis_key)
            if data is None:
                return None
            return self._deserialize_value(data)
        except Exception as e:
            logger.warning("Error reading from Redis cache: %s", type(e).__name__)
            try:
                await self._client.delete(redis_key)
            except Exception:
                pass
            return None

    async def set(self, key: K, value: V) -> None:
        """Set a value in Redis cache with TTL."""
        redis_key = self._serialize_key(key)
        payload = self._serialize_value(value)
        await self._client.setex(redis_key, self._jittered_ttl(), payload)

    async def get_many(self, keys: Sequence[K]) -> list[V | None]:
        """Get multiple values from Redis in a single round-trip."""
        if not keys:
            return []

        redis_keys = [self._serialize_key(key) for key in keys]
        try:
            payloads = await self._client.mget(redis_keys)
        except Exception as e:
            logger.warning(
                "Error reading from Redis cache in batch: %s", type(e).__name__
            )
            return [None] * len(redis_keys)

        results: list[V | None] = []
        for redis_key, payload in zip(redis_keys, payloads):
            if payload is None:
                results.append(None)
                continue

            try:
                results.append(self._deserialize_value(payload))
            except Exception as e:
                logger.warning(
                    "Error deserializing Redis cache value: %s", type(e).__name__
                )
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
                self._serialize_key(key),
                self._jittered_ttl(),
                self._serialize_value(value),
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


def create_redis_client(url: str, *, decode_responses: bool = False) -> Redis:
    """Create an async Redis client backed by a shared connection pool.

    Args:
        url: Redis connection URL.
        decode_responses: When True the client returns ``str`` instead of
            ``bytes``. ``decode_responses`` is a connection-level setting in
            redis-py, so pools are keyed by ``(url, decode_responses)`` — a
            str-decoding caller and a bytes caller on the same URL each get their
            own bounded pool rather than clobbering one another.
    """
    if Redis is None or ConnectionPool is None:
        raise RuntimeError("redis package is not installed.")

    from core.config.cache import get_redis_cache_config

    config = get_redis_cache_config()
    pool_key = (url, decode_responses)

    with _shared_pools_lock:
        pool = _shared_pools.get(pool_key)
        if pool is None:
            # Bound the pool so a burst of concurrent callers can't open an
            # unlimited number of Redis connections (and exhaust the server).
            pool = ConnectionPool.from_url(
                url,
                max_connections=config.max_connections,
                health_check_interval=config.health_check_interval,
                decode_responses=decode_responses,
            )
            _shared_pools[pool_key] = pool

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
