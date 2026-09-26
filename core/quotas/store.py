"""
Storage backends for per-key usage quotas.

A quota counter is keyed by ``subject:window:period`` (the period embeds the
calendar date, so counters reset naturally when the window rolls over). The
store needs atomic increment + read, plus — optionally, but both built-in
stores have it — :meth:`check_and_incr_many`, the all-or-nothing
check-and-consume the request path uses; :class:`InMemoryQuotaStore` is the
single-process default and :class:`RedisQuotaStore` shares counters across
workers with a TTL bounding stale keys.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple, Protocol, runtime_checkable

from core.observability.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class QuotaStore(Protocol):
    """Atomic counter store for quota windows."""

    async def get(self, window_key: str) -> int: ...

    async def incr(self, window_key: str, amount: int, ttl_seconds: int) -> int: ...

    async def get_many(self, window_keys: Sequence[str]) -> list[int]:
        """Read many counters in one round-trip.

        Implementations MUST return **exactly one count per key, in the key
        order**, using ``0`` for a counter that does not exist yet — the
        manager zips the result back against its plan under ``strict=True`` to
        match each count to its window.
        """
        ...

    async def incr_many(self, items: Sequence[tuple[str, int, int]]) -> list[int]:
        """Increment many counters in one round-trip.

        Implementations MUST return **exactly one post-increment value per
        item, in the item order** — the manager zips the result back against
        its plan under ``strict=True`` to attach each new value to its window.
        """
        ...


class BatchConsumeResult(NamedTuple):
    """Outcome of an atomic all-or-nothing :meth:`check_and_incr_many`.

    ``rejected`` is the index of the first item whose limit would be exceeded
    (nothing was incremented), or ``None`` when every counter was consumed.
    ``values`` holds, on success, the post-increment value of every item; on
    rejection, a single element — the rejected counter's current value.
    """

    rejected: int | None
    values: list[int]


# One round trip, atomic on the Redis server: check EVERY counter against its
# limit, and only if all have room INCRBY each one and give it a TTL if it has
# none. The previous MGET → (Python check) → INCRBY pipeline → EXPIRE pipeline
# had two holes: concurrent requests all passed the MGET and then all
# incremented (overshooting the limit by up to the concurrency), and a crash
# between INCRBY and EXPIRE left a counter with no TTL at all. ``TTL < 0``
# (-1: no expiry) rather than ``EXPIRE … NX`` keeps the script valid on
# Redis < 7 and also repairs a counter orphaned by that old crash window.
# Single-node keys (no Cluster hash tag), like the idempotency script.
#   KEYS[i] counter    ARGV[3i-2] amount   ARGV[3i-1] limit   ARGV[3i] ttl
#   returns {0, i, used} = rejected at item i | {1, v1, v2, ...} = consumed
CHECK_AND_INCR_LUA = """
for i = 1, #KEYS do
  local used = tonumber(redis.call('GET', KEYS[i]) or '0')
  if used + tonumber(ARGV[3*i-2]) > tonumber(ARGV[3*i-1]) then
    return {0, i, used}
  end
end
local out = {1}
for i = 1, #KEYS do
  out[#out + 1] = redis.call('INCRBY', KEYS[i], ARGV[3*i-2])
  local ttl = tonumber(ARGV[3*i])
  if ttl > 0 and redis.call('TTL', KEYS[i]) < 0 then
    redis.call('EXPIRE', KEYS[i], ttl)
  end
end
return out
"""


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

    async def check_and_incr_many(
        self, items: Sequence[tuple[str, int, int, int]]
    ) -> BatchConsumeResult:
        """Atomic check-all-then-increment-all (``key, amount, limit, ttl``).

        No ``await`` between the check and the increments, so on one event
        loop this is as atomic as the Redis script it mirrors.
        """
        for index, (key, amount, limit, _ttl) in enumerate(items):
            used = self._counts.get(key, 0)
            if used + amount > limit:
                return BatchConsumeResult(index, [used])
        values = []
        for key, amount, _limit, _ttl in items:
            self._counts[key] = self._counts.get(key, 0) + amount
            values.append(self._counts[key])
        return BatchConsumeResult(None, values)


class RedisQuotaStore:
    """Redis-backed counter: ``INCRBY`` + ``EXPIRE`` on first write."""

    def __init__(self, redis_client: object, prefix: str = "quota:") -> None:
        self._redis = redis_client
        self._prefix = prefix
        self._check_and_incr_script: Any = None

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
            for (key, amount, ttl), new_value in zip(items, new_values, strict=True)
            if new_value == amount and ttl > 0
        ]
        if fresh:
            expire_pipe = self._redis.pipeline(transaction=False)  # type: ignore[attr-defined]
            for key, ttl in fresh:
                expire_pipe.expire(key, ttl)
            await expire_pipe.execute()
        return new_values

    async def check_and_incr_many(
        self, items: Sequence[tuple[str, int, int, int]]
    ) -> BatchConsumeResult:
        """Check every limit and consume every counter in one atomic script.

        ``items`` are ``(key, amount, limit, ttl_seconds)``. Either all
        counters are incremented (and TTL-anchored) or none is.
        """
        if not items:
            return BatchConsumeResult(None, [])
        if self._check_and_incr_script is None:
            self._check_and_incr_script = self._redis.register_script(  # type: ignore[attr-defined]
                CHECK_AND_INCR_LUA
            )
        args: list[int] = []
        for _key, amount, limit, ttl in items:
            args.extend((amount, limit, ttl))
        result = await self._check_and_incr_script(
            keys=[self._prefix + key for key, *_ in items], args=args
        )
        if int(result[0]) == 0:
            return BatchConsumeResult(int(result[1]) - 1, [int(result[2])])
        return BatchConsumeResult(None, [int(value) for value in result[1:]])


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
