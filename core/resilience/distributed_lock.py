"""Redis-backed distributed lock for multi-replica coordination.

In a single process, in-memory coordination (e.g. ``core.cache.single_flight``)
is enough. Across replicas it is not: scheduled jobs, cron triggers, and
"run-once" startup tasks will fire on *every* pod. This module provides a
correct distributed mutex so exactly one replica performs such work.

Correctness properties
-----------------------
- **Mutual exclusion** via ``SET key token NX PX ttl`` — only the first caller
  wins; the key auto-expires so a crashed holder never deadlocks the lock.
- **Safe release** via a compare-and-delete Lua script: a holder only deletes
  the key if it still owns the unique token, so a lock that already expired and
  was re-acquired by another replica is never released out from under it.
- **Optional auto-renew** (watchdog): a background task extends the TTL while
  the critical section runs, so long tasks are not cut off, while crashes still
  release the lock within one TTL.

The Redis client is injected (typed ``Any`` to avoid a hard import here); use
:func:`get_distributed_lock` to build one from the cache configuration.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Atomic compare-and-delete: only release if we still hold the token.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

# Atomic compare-and-renew: only extend TTL if we still hold the token.
_RENEW_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""

_DEFAULT_TTL_MS = 30_000
_KEY_PREFIX = "baselithcore:lock:"


class LockError(Exception):
    """Base class for distributed-lock errors."""


class LockNotAcquired(LockError):
    """Raised by :meth:`DistributedLock.guard` when the lock cannot be taken."""


class DistributedLock:
    """A single distributed mutex identified by ``name``.

    Not safe to share a single instance across concurrent acquirers: create one
    instance per critical section (or use :func:`get_distributed_lock`).
    """

    def __init__(
        self,
        redis_client: Any,
        name: str,
        *,
        ttl_ms: int = _DEFAULT_TTL_MS,
        auto_renew: bool = False,
    ) -> None:
        """Initialize the lock.

        Args:
            redis_client: An ``redis.asyncio.Redis``-compatible client.
            name: Logical lock name (namespaced with a fixed prefix).
            ttl_ms: Lock time-to-live in milliseconds. A crashed holder releases
                the lock after at most this long.
            auto_renew: If True, run a watchdog that extends the TTL at roughly
                one third of ``ttl_ms`` for the duration of the held lock.
        """
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive.")
        self._redis = redis_client
        self._key = f"{_KEY_PREFIX}{name}"
        self._ttl_ms = ttl_ms
        self._auto_renew = auto_renew
        self._token: Optional[str] = None
        self._renew_task: Optional[asyncio.Task[None]] = None

    @property
    def held(self) -> bool:
        """Whether this instance currently believes it holds the lock."""
        return self._token is not None

    async def acquire(
        self,
        *,
        blocking: bool = True,
        timeout: Optional[float] = None,
        retry_interval: float = 0.1,
    ) -> bool:
        """Attempt to acquire the lock.

        Args:
            blocking: If True, keep retrying until acquired or ``timeout``.
            timeout: Max seconds to wait when blocking (None = wait forever).
            retry_interval: Seconds between attempts when blocking.

        Returns:
            True if the lock was acquired, False otherwise.
        """
        token = os.urandom(16).hex()
        deadline = None if timeout is None else (await _loop_time()) + timeout

        while True:
            acquired = await self._redis.set(self._key, token, nx=True, px=self._ttl_ms)
            if acquired:
                self._token = token
                if self._auto_renew:
                    self._renew_task = asyncio.create_task(self._renew_loop())
                logger.debug("Acquired distributed lock %s", self._key)
                return True

            if not blocking:
                return False
            if deadline is not None and (await _loop_time()) >= deadline:
                return False
            await asyncio.sleep(retry_interval)

    async def release(self) -> bool:
        """Release the lock if still owned by this instance.

        Returns:
            True if this instance owned and deleted the key, False otherwise.
        """
        if self._token is None:
            return False
        await self._cancel_renew()
        token, self._token = self._token, None
        try:
            result = await self._redis.eval(_RELEASE_LUA, 1, self._key, token)
        except Exception as exc:  # noqa: BLE001 — release must not raise
            logger.warning("Lock release failed for %s: %s", self._key, exc)
            return False
        released = bool(result)
        if not released:
            logger.warning(
                "Lock %s was not owned at release (expired and re-acquired?)",
                self._key,
            )
        return released

    async def renew(self, ttl_ms: Optional[int] = None) -> bool:
        """Extend the lock TTL if still owned.

        Args:
            ttl_ms: New TTL in ms (defaults to the configured TTL).

        Returns:
            True if the TTL was extended, False if the lock is no longer owned.
        """
        if self._token is None:
            return False
        ttl = ttl_ms if ttl_ms is not None else self._ttl_ms
        result = await self._redis.eval(_RENEW_LUA, 1, self._key, self._token, ttl)
        return bool(result)

    async def _renew_loop(self) -> None:
        """Watchdog: periodically extend the TTL while the lock is held."""
        interval = max(self._ttl_ms / 3.0 / 1000.0, 0.05)
        try:
            while self._token is not None:
                await asyncio.sleep(interval)
                if not await self.renew():
                    logger.warning("Auto-renew lost lock %s", self._key)
                    return
        except asyncio.CancelledError:  # normal on release
            raise

    async def _cancel_renew(self) -> None:
        """Stop the watchdog task, if running."""
        if self._renew_task is not None:
            self._renew_task.cancel()
            try:
                await self._renew_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._renew_task = None

    async def __aenter__(self) -> "DistributedLock":
        await self.acquire()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.release()

    @asynccontextmanager
    async def guard(
        self, *, timeout: Optional[float] = None
    ) -> AsyncIterator["DistributedLock"]:
        """Context manager that acquires (non-forever) and always releases.

        Raises:
            LockNotAcquired: If the lock cannot be acquired within ``timeout``.
        """
        ok = await self.acquire(blocking=timeout is not None, timeout=timeout)
        if not ok:
            raise LockNotAcquired(f"Could not acquire lock {self._key!r}.")
        try:
            yield self
        finally:
            await self.release()


async def _loop_time() -> float:
    """Return the running loop's monotonic clock (test-friendly indirection)."""
    return asyncio.get_running_loop().time()


def get_distributed_lock(
    name: str,
    *,
    ttl_ms: int = _DEFAULT_TTL_MS,
    auto_renew: bool = False,
    redis_url: Optional[str] = None,
) -> DistributedLock:
    """Build a :class:`DistributedLock` backed by the cache Redis.

    Args:
        name: Logical lock name.
        ttl_ms: Lock TTL in milliseconds.
        auto_renew: Enable the TTL watchdog.
        redis_url: Override the Redis URL (defaults to the cache config URL).

    Returns:
        A ready-to-use lock instance.
    """
    from core.cache.redis_cache import create_redis_client
    from core.config import get_redis_cache_config

    url = redis_url if redis_url is not None else get_redis_cache_config().url
    return DistributedLock(
        create_redis_client(url), name, ttl_ms=ttl_ms, auto_renew=auto_renew
    )


__all__ = [
    "DistributedLock",
    "LockError",
    "LockNotAcquired",
    "get_distributed_lock",
]
