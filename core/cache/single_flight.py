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

With ``WEB_CONCURRENCY>1`` or several pods, that only deduplicates within one
event loop: W workers still make W identical calls. :class:`LayeredSingleFlight`
adds a cross-worker layer on top, but it is only meaningful when the backing
cache is shared — see that class for the full winner-publishes / loser-rereads
path, and :func:`build_single_flight` for the opt-in wiring.
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

    Implementation is async-safe within a single event loop only. For
    coalescing across worker processes or pods, wrap this in a
    :class:`LayeredSingleFlight` — see that class for why a distributed lock
    alone is not enough.
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
            from core.cache.redis_cache import create_redis_client
            from core.config.cache import get_redis_cache_config

            # Shared, bounded pool (per URL) with the cache's socket deadlines:
            # an unresponsive Redis must fail the lock acquisition rather than
            # hang the caller (and every waiter) forever, and a burst of
            # single-flight users must not open connections without limit.
            redis_client = create_redis_client(
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


class LayeredSingleFlight(Generic[T]):
    """In-process coalescing stacked on top of optional cross-worker coalescing.

    A distributed lock on its own does **not** give coalescing — it gives
    mutual exclusion. The worker that loses the lock still needs somewhere to
    read the winner's result from, otherwise it either waits and then
    recomputes (no saving) or fails. So the full path is:

    1. **In-process layer** (:class:`SingleFlight`) collapses the N concurrent
       coroutines of *this* worker down to one. Without it, N coroutines would
       each race for the Redis lock and N-1 would enter the polling path — the
       stampede simply moves from the backend onto Redis.
    2. **Cross-worker layer** (:class:`RedisSingleFlight`) elects one worker
       among W. The winner computes and *publishes* the value into the shared
       cache; the losers poll ``recheck`` (a read of that same shared cache)
       and return the winner's value.

    The second layer is therefore only meaningful when the backing cache is
    genuinely shared. Against a process-local store (a dict, ``cachetools``)
    the loser's ``recheck`` can never observe the winner's write, so it would
    pay the polling latency and then recompute anyway — strictly worse than
    plain in-process coalescing. Pass ``distributed=None`` in that case.

    Fail-open by construction: when ``distributed`` is None, or when Redis is
    unreachable (:class:`RedisSingleFlight` catches and runs the factory
    directly), behaviour degrades to exactly the in-process semantics. A cache
    fill must never fail because a coordination side-channel is down.
    """

    def __init__(self, distributed: RedisSingleFlight[T] | None = None) -> None:
        """
        Args:
            distributed: Cross-worker layer, or None for in-process only.
        """
        self._local: SingleFlight[T] = SingleFlight()
        self._distributed = distributed

    @property
    def is_distributed(self) -> bool:
        """Whether the cross-worker layer is active (testing/diagnostics)."""
        return self._distributed is not None

    async def do(
        self,
        key: Any,
        factory: Callable[[], Awaitable[T]],
        *,
        recheck: Callable[[], Awaitable[T | None]] | None = None,
    ) -> T:
        """Run ``factory`` once per key, locally and (if enabled) fleet-wide.

        Args:
            key: Hashable coalescing key — use the same key the shared cache
                is keyed by, so ``recheck`` and the lock agree on identity.
            factory: Produces the value and is expected to publish it into the
                shared cache before returning.
            recheck: Re-reads the shared cache; how a losing worker obtains the
                winner's result. Ignored when there is no distributed layer.
        """
        distributed = self._distributed
        if distributed is None:
            return await self._local.do(key, factory)

        async def _via_shared_lock() -> T:
            return await distributed.do(key, factory, recheck=recheck)

        return await self._local.do(key, _via_shared_lock)


def build_single_flight(
    *,
    shared_cache: bool,
    ttl_seconds: float = 30.0,
    key_prefix: str = "baselithcore:singleflight",
) -> LayeredSingleFlight[Any]:
    """Build a coordinator, enabling the cross-worker layer only when it can work.

    Two independent conditions must both hold, and neither is the presence of
    a Redis URL: ``CACHE_REDIS_URL`` ships with a non-empty default while
    ``CACHE_BACKEND`` defaults to ``local``, so testing the URL alone would
    switch a stock config onto a Redis that is not there.

    1. ``CACHE_CROSS_WORKER_SINGLE_FLIGHT`` is set — an explicit opt-in, since
       this puts a network round-trip on a hot cache-miss path.
    2. ``shared_cache`` — the caller confirms its backing cache is actually
       shared between workers, which is what lets a losing worker read the
       winner's result.

    Args:
        shared_cache: True when the caller's cache is cross-worker visible.
        ttl_seconds: Lock TTL; bounds both waiter blocking and orphan locks.
        key_prefix: Namespace for lock keys.

    Returns:
        A coordinator that is in-process only unless both conditions hold.
    """
    if not shared_cache:
        return LayeredSingleFlight()

    try:
        from core.config import get_cache_config

        if not get_cache_config().cross_worker_single_flight:
            return LayeredSingleFlight()
        distributed: RedisSingleFlight[Any] = RedisSingleFlight(
            ttl_seconds=ttl_seconds, key_prefix=key_prefix
        )
    except Exception as exc:
        # Config unreadable or redis-py missing: in-process coalescing still
        # works, so never let coordination setup break the caller.
        logger.warning("cross_worker_single_flight_disabled: %s", exc)
        return LayeredSingleFlight()

    logger.info("Cross-worker single-flight enabled (prefix=%s)", key_prefix)
    return LayeredSingleFlight(distributed)


__all__ = [
    "LayeredSingleFlight",
    "RedisSingleFlight",
    "SingleFlight",
    "build_single_flight",
]
