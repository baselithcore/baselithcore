"""Unit tests for the Redis-backed distributed lock.

Uses a minimal in-memory async fake that implements just the Redis surface the
lock relies on: ``set`` with ``nx``/``px`` and ``eval`` for the compare-and-
delete / compare-and-renew Lua scripts, with TTL honoured via a fake clock.
"""

import asyncio

import pytest

from core.resilience.distributed_lock import DistributedLock, LockNotAcquired


class FakeRedis:
    """In-memory async stand-in for redis.asyncio.Redis (lock subset only)."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self.now = 0.0  # fake monotonic clock in seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _purge(self, key: str) -> None:
        exp = self._expiry.get(key)
        if exp is not None and self.now >= exp:
            self._store.pop(key, None)
            self._expiry.pop(key, None)

    async def set(self, key, value, nx=False, px=None):  # noqa: ANN001
        self._purge(key)
        if nx and key in self._store:
            return None
        self._store[key] = value
        if px is not None:
            self._expiry[key] = self.now + px / 1000.0
        return True

    async def get(self, key):  # noqa: ANN001
        self._purge(key)
        return self._store.get(key)

    async def eval(self, script, numkeys, key, *args):  # noqa: ANN001
        self._purge(key)
        token = args[0]
        if self._store.get(key) != token:
            return 0
        if "del" in script:
            self._store.pop(key, None)
            self._expiry.pop(key, None)
            return 1
        if "pexpire" in script:
            self._expiry[key] = self.now + int(args[1]) / 1000.0
            return 1
        return 0


@pytest.fixture
def redis():
    return FakeRedis()


async def test_acquire_and_release(redis):
    lock = DistributedLock(redis, "job", ttl_ms=5000)
    assert await lock.acquire() is True
    assert lock.held is True
    assert await redis.get("baselithcore:lock:job") is not None
    assert await lock.release() is True
    assert lock.held is False
    assert await redis.get("baselithcore:lock:job") is None


async def test_mutual_exclusion(redis):
    a = DistributedLock(redis, "job", ttl_ms=5000)
    b = DistributedLock(redis, "job", ttl_ms=5000)
    assert await a.acquire() is True
    # Second contender cannot take it non-blocking.
    assert await b.acquire(blocking=False) is False
    await a.release()
    # Now available.
    assert await b.acquire(blocking=False) is True


async def test_release_only_if_owner(redis):
    a = DistributedLock(redis, "job", ttl_ms=5000)
    await a.acquire()
    # Simulate expiry + re-acquisition by another holder.
    redis.advance(6.0)
    b = DistributedLock(redis, "job", ttl_ms=5000)
    assert await b.acquire(blocking=False) is True
    # a's release must NOT remove b's lock (token mismatch).
    assert await a.release() is False
    assert await redis.get("baselithcore:lock:job") is not None
    assert await b.release() is True


async def test_blocking_timeout(redis):
    a = DistributedLock(redis, "job", ttl_ms=60000)
    await a.acquire()
    b = DistributedLock(redis, "job", ttl_ms=60000)
    # Should give up after the timeout rather than hang.
    acquired = await asyncio.wait_for(
        b.acquire(blocking=True, timeout=0.2, retry_interval=0.05), timeout=2.0
    )
    assert acquired is False


async def test_guard_acquires_and_releases(redis):
    lock = DistributedLock(redis, "job", ttl_ms=5000)
    async with lock.guard(timeout=1.0):
        assert lock.held is True
    assert lock.held is False


async def test_guard_raises_when_unavailable(redis):
    held = DistributedLock(redis, "job", ttl_ms=60000)
    await held.acquire()
    contender = DistributedLock(redis, "job", ttl_ms=60000)
    with pytest.raises(LockNotAcquired):
        async with contender.guard(timeout=0.1):
            pass


async def test_renew_extends_ttl(redis):
    lock = DistributedLock(redis, "job", ttl_ms=2000)
    await lock.acquire()
    redis.advance(1.5)
    assert await lock.renew() is True
    redis.advance(1.5)  # 3.0s total, but renewed at 1.5s -> still valid
    assert await redis.get("baselithcore:lock:job") is not None
    await lock.release()


async def test_renew_fails_when_not_held(redis):
    lock = DistributedLock(redis, "job", ttl_ms=2000)
    assert await lock.renew() is False


async def test_context_manager(redis):
    async with DistributedLock(redis, "job", ttl_ms=5000) as lock:
        assert lock.held is True
    assert await redis.get("baselithcore:lock:job") is None


async def test_auto_renew_watchdog(redis):
    # ttl 300ms -> watchdog renews ~every 100ms; hold past one TTL.
    lock = DistributedLock(redis, "job", ttl_ms=300, auto_renew=True)
    await lock.acquire()
    await asyncio.sleep(0.45)  # real time; watchdog uses real asyncio.sleep
    # Watchdog kept it alive (renew uses fake clock at now=0, so key present).
    assert lock.held is True
    await lock.release()
    assert lock._renew_task is None
