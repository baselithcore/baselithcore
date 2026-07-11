"""Per-key single-flight coalescing for cache miss → fill paths.

A cache miss for a popular key triggers an expensive backend call (LLM
prompt, vector search, etc). Without coordination, every concurrent caller
that arrives during the in-flight call independently re-issues the same
request — the well-known *thundering herd* / *cache stampede* problem.

``SingleFlight`` coalesces concurrent calls for the same key: only the first
caller executes the supplied factory; subsequent waiters share the eventual
result (or exception).

Usage::

    sf = SingleFlight()

    async def fetch(prompt: str) -> str:
        cached = await cache.get(prompt)
        if cached is not None:
            return cached
        return await sf.do(prompt, lambda: expensive_call(prompt))
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

from core.observability.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class SingleFlight(Generic[T]):
    """Coalesce concurrent calls keyed by hashable identity.

    Implementation is async-safe within a single event loop. Cross-process
    coalescing (e.g. across worker pods) requires a distributed lock such as
    Redis ``SET NX EX`` — see :class:`RedisSingleFlight` if/when that becomes
    a real bottleneck.
    """

    def __init__(self) -> None:
        self._inflight: dict[Any, asyncio.Future[T]] = {}
        self._lock = asyncio.Lock()

    async def do(self, key: Any, factory: Callable[[], Awaitable[T]]) -> T:
        """Run ``factory`` exactly once for ``key`` while concurrent callers wait."""
        async with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                future = existing
                owner = False
            else:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._inflight[key] = future
                owner = True

        if owner:
            try:
                value = await factory()
            except BaseException as exc:
                future.set_exception(exc)
                async with self._lock:
                    self._inflight.pop(key, None)
                raise
            future.set_result(value)
            async with self._lock:
                self._inflight.pop(key, None)
            return value

        return await future

    def in_flight(self) -> int:
        """Return the number of currently coalesced keys (testing/diagnostics)."""
        return len(self._inflight)


# Release only when the lock still holds OUR token: an unguarded DEL after a
# TTL expiry would delete the lock a *different* worker has since acquired.
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


class RedisSingleFlight(Generic[T]):
    """Cross-worker single-flight via a Redis ``SET NX EX`` lock.

    The in-process :class:`SingleFlight` coalesces within one event loop; in a
    multi-worker deployment each pod still stampedes the backend on a popular
    cache miss. This variant elects one owner across workers:

    * the **owner** (winner of ``SET NX EX``) runs ``factory`` and releases
      the lock with a token-guarded Lua script (never deletes a lock another
      worker re-acquired after a TTL expiry);
    * **waiters** poll with exponential backoff, re-checking the caller's
      cache via ``recheck`` until the owner finishes (lock released) or the
      lock TTL elapses.

    Fail-open by design: on timeout, Redis errors, or a still-missing value
    after the owner finished, the waiter computes ``factory`` itself —
    availability over strict deduplication (an occasional duplicate upstream
    call, never a deadlocked request).
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        *,
        url: str | None = None,
        ttl_seconds: float = 30.0,
        poll_interval: float = 0.05,
        key_prefix: str = "baselithcore:singleflight",
    ) -> None:
        """
        Args:
            redis_client: Pre-built ``redis.asyncio`` client; overrides ``url``.
            url: Redis connection URL; defaults to the cache Redis config.
            ttl_seconds: Lock TTL — the upper bound a waiter blocks, and the
                deadlock bound if the owner crashes mid-computation.
            poll_interval: Initial waiter poll delay (backs off ×1.5, cap 0.5s).
            key_prefix: Namespace for lock keys.
        """
        if redis_client is None:
            import redis.asyncio as redis_async

            from core.config.cache import get_redis_cache_config

            redis_client = redis_async.Redis.from_url(
                url or get_redis_cache_config().url, decode_responses=True
            )
        self._redis: Any = redis_client
        self._ttl = ttl_seconds
        self._poll = poll_interval
        self._prefix = key_prefix

    def _name(self, key: Any) -> str:
        return f"{self._prefix}:{key}"

    async def do(
        self,
        key: Any,
        factory: Callable[[], Awaitable[T]],
        *,
        recheck: Callable[[], Awaitable[T | None]] | None = None,
    ) -> T:
        """Run ``factory`` once across workers; waiters resolve via ``recheck``.

        ``recheck`` re-reads the caller's cache (the owner is expected to
        populate it); without one, waiters simply run ``factory`` after the
        owner finishes — still bounding the stampede to two calls, not N.
        """
        import uuid

        name = self._name(key)
        token = uuid.uuid4().hex
        try:
            acquired = await self._redis.set(
                name, token, nx=True, ex=max(int(self._ttl), 1)
            )
        except Exception as exc:  # Redis down: degrade to direct execution
            logger.warning("redis_single_flight_unavailable: %s", exc)
            return await factory()

        if acquired:
            try:
                return await factory()
            finally:
                try:
                    await self._redis.eval(_RELEASE_LUA, 1, name, token)
                except Exception as exc:  # TTL will reap the lock
                    logger.warning("redis_single_flight_release_failed: %s", exc)

        # Waiter path: poll until the owner releases or the TTL elapses.
        deadline = asyncio.get_running_loop().time() + self._ttl
        delay = self._poll
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 0.5)
            try:
                if recheck is not None:
                    value = await recheck()
                    if value is not None:
                        return value
                if not await self._redis.exists(name):
                    break  # owner finished (or lock expired)
            except Exception as exc:
                logger.warning("redis_single_flight_wait_failed: %s", exc)
                break

        if recheck is not None:
            value = await recheck()
            if value is not None:
                return value
        return await factory()
