"""
Distributed rate limiting.

Redis-backed sliding-window rate limiter with an in-memory fallback, used by
``core.middleware.security.SecurityManager`` on every authenticated request.
Extracted from ``core/middleware/security.py`` to keep modules under the
500-line cap; the class is re-exported there for backward compatibility.

Window model
------------
A fixed window admits up to **twice** the limit across a boundary (N requests
at t=59s, N more at t=61s). This limiter keeps one counter per window *index*
(``floor(now / window)``) and weights the previous window by how much of it
still overlaps the trailing ``window`` seconds::

    estimate = previous * (window - elapsed) / window + current

The estimate is what is compared against the limit, so the boundary burst is
rejected while steady traffic at the limit is admitted. Both counters are
bumped/read in one atomic Lua round trip — the same per-request cost as the
old fixed window. Keys carry a Redis Cluster hash tag so both windows of one
identifier hash to the same slot.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from fastapi import HTTPException, status

from core.cache.redis_cache import create_redis_client
from core.config.cache import get_redis_cache_config
from core.config.security import get_security_config
from core.middleware._security_metrics import SECURITY_EVENTS
from core.observability.logging import get_logger

logger = get_logger(__name__)

# Atomic sliding-window step: INCR the current window (EXPIRE on first hit,
# sized to TWO windows so the key is still readable as the *previous* window
# during the next one) and read the previous window's count. One round trip;
# the script runs atomically server-side, so the TTL is always set together
# with the first increment (no TOCTOU window).
_RATE_LIMIT_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local previous = redis.call('GET', KEYS[2])
return {current, tonumber(previous) or 0}
"""


def _sliding_estimate(
    prev: int, current: int, window_seconds: int, elapsed: float
) -> float:
    """Weighted request count over the trailing ``window_seconds``.

    ``elapsed`` is how far into the current window ``now`` sits; the previous
    window contributes proportionally to the part of it still inside the
    trailing interval.
    """
    weight = max(0.0, window_seconds - elapsed) / window_seconds
    return prev * weight + current


def _sliding_retry_after(
    prev: int, current: int, limit: int, window_seconds: int, elapsed: float
) -> int:
    """Seconds until one more request fits under ``limit`` (assuming no traffic).

    Solves the estimate inequality for the admission time, first inside the
    current window (only the previous window's weight decays) and, when the
    current window is already full, after the rollover — at which point the
    current count becomes the decaying previous window.
    """
    headroom = limit - current - 1
    if headroom >= 0:
        if prev <= 0:
            return 0
        admit_at = window_seconds * (1.0 - headroom / prev)
        return max(0, math.ceil(round(admit_at - elapsed, 6)))
    to_rollover = window_seconds - elapsed
    decay = window_seconds * (1.0 - (limit - 1) / current)
    return max(0, math.ceil(round(to_rollover + decay, 6)))


def _rate_limit_headers(limit: int, current: int, reset_seconds: int) -> dict[str, str]:
    """Build IETF ``RateLimit`` + ``Retry-After`` headers for a 429 response."""
    reset = max(0, reset_seconds)
    return {
        "Retry-After": str(reset),
        "RateLimit-Limit": str(limit),
        "RateLimit-Remaining": str(max(0, limit - current)),
        "RateLimit-Reset": str(reset),
    }


def _raise_rate_limited(headers: dict[str, str]) -> None:
    """Emit the rate-limit metric and raise a 429 carrying standard headers."""
    SECURITY_EVENTS.labels(reason="rate_limited").inc()
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Rate limit exceeded, please try again shortly.",
        headers=headers,
    )


def _resolve_fail_mode(configured: str | None) -> str:
    """Return the effective ``RATE_LIMIT_FAIL_MODE``.

    An explicit value wins. Unset means ``closed`` in production *when the
    deployment declared a Redis cache backend* — the limiter backs brute-force
    and cost controls, and an outage of the shared counter must not silently
    widen them to N x across replicas — and ``open`` everywhere else: outside
    production, and in a deployment that never configured Redis, where the
    per-process window is the design rather than a degraded state (same rule
    as the A2A nonce ledger and the AP2 replay guard).
    """
    if configured in ("open", "closed"):
        return configured
    from core.config.environment import is_production_env

    if not is_production_env():
        return "open"
    try:
        from core.config import get_storage_config

        redis_declared = getattr(get_storage_config(), "cache_backend", "") == "redis"
    except Exception:  # pragma: no cover - config unavailable in minimal envs
        redis_declared = False
    return "closed" if redis_declared else "open"


def _window_position(window_seconds: int) -> tuple[int, float]:
    """Return ``(window_index, seconds_elapsed_in_window)`` for *now*."""
    now = time.time()
    index = int(now // window_seconds)
    return index, now - index * window_seconds


class RateLimiter:
    """
    Distributed sliding-window rate limiter by role/key/IP, using Redis.
    """

    def __init__(self) -> None:
        cache_config = get_redis_cache_config()
        self._prefix = cache_config.cache_prefix + ":ratelimit:"
        self._redis = None
        self._rate_limit_script: Any = None
        # identifier -> (count_in_current_window, window_index, previous_count)
        self._fallback: dict[str, tuple[int, int, int]] = {}
        self._fallback_lock = asyncio.Lock()
        # Amortizes the fallback prune: without it the O(n) sweep ran on
        # EVERY check past 1000 entries — under one global lock, exactly when
        # Redis is down and load is worst.
        self._fallback_checks_since_prune = 0
        # "open": degrade to per-process memory on Redis loss (limit becomes
        # ~N x across replicas). "closed": 503 instead — the limit is treated
        # as a security control that must not silently widen. Unset resolves
        # here, not at config load: the production posture can still be armed
        # after SecurityConfig is built (assume_production_when_undeclared).
        self._fail_mode = _resolve_fail_mode(get_security_config().rate_limit_fail_mode)
        try:
            redis_client = create_redis_client(cache_config.url)
            self._redis = redis_client
            self._rate_limit_script = redis_client.register_script(_RATE_LIMIT_LUA)
        except Exception as e:
            logger.warning(
                "Redis rate limiter unavailable during initialization (%s), using in-memory fallback",
                type(e).__name__,
            )

    async def close(self) -> None:
        """Close the underlying Redis connection."""
        if self._redis is not None:
            await self._redis.close()

    def _degraded(self, window_seconds: int) -> None:
        """Fail-closed guard: in ``closed`` mode a missing/failed Redis backend
        rejects the request with 503 instead of silently widening the limit to
        N x across replicas."""
        if self._fail_mode != "closed":
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Rate limiting backend unavailable and RATE_LIMIT_FAIL_MODE="
                "closed; request rejected."
            ),
            headers={"Retry-After": str(window_seconds)},
        )

    @staticmethod
    def _enforce(
        prev: int, current: int, limit: int, window_seconds: int, elapsed: float
    ) -> None:
        """Raise 429 when the weighted estimate exceeds ``limit``."""
        estimate = _sliding_estimate(prev, current, window_seconds, elapsed)
        if estimate > limit:
            reset = _sliding_retry_after(prev, current, limit, window_seconds, elapsed)
            _raise_rate_limited(_rate_limit_headers(limit, math.ceil(estimate), reset))

    async def _check_fallback(
        self, identifier: str, limit: int, window_seconds: int
    ) -> None:
        """Best-effort local sliding-window fallback when Redis is unavailable."""
        async with self._fallback_lock:
            index, elapsed = _window_position(window_seconds)
            count, stored_index, prev = self._fallback.get(identifier, (0, index, 0))
            if stored_index != index:
                # Roll the window: the old current count becomes the previous
                # window only if it was the immediately preceding one.
                prev = count if stored_index == index - 1 else 0
                count = 0
            count += 1
            self._fallback[identifier] = (count, index, prev)

            # Prune entries older than the previous window to prevent unbounded
            # memory growth. Amortized: at most one O(n) sweep per 100 checks,
            # and only once the map is large — never on every request under
            # the global lock.
            self._fallback_checks_since_prune += 1
            if len(self._fallback) > 1000 and self._fallback_checks_since_prune >= 100:
                self._fallback_checks_since_prune = 0
                self._fallback = {
                    k: v for k, v in self._fallback.items() if v[1] >= index - 1
                }

            self._enforce(prev, count, limit, window_seconds, elapsed)

    async def check(
        self, identifier: str, limit: int | None, window_seconds: int
    ) -> None:
        """
        Check if identifier is within rate limit.

        Args:
            identifier: Unique identifier (role:key format)
            limit: Maximum requests per window
            window_seconds: Time window in seconds

        Raises:
            HTTPException: 429 if rate limit exceeded
        """
        if limit is None or limit <= 0:
            return

        if self._redis is None:
            self._degraded(window_seconds)
            await self._check_fallback(identifier, limit, window_seconds)
            return

        index, elapsed = _window_position(window_seconds)
        # ``{...}`` is a Redis Cluster hash tag: both window keys of one
        # identifier land on the same slot, which a multi-key script requires.
        base = f"{{{self._prefix}{identifier}}}"
        keys = [f"{base}:{index}", f"{base}:{index - 1}"]

        try:
            # Single atomic Lua round trip: INCR the current window (EXPIRE on
            # first hit) and read the previous one, at the same per-request
            # Redis latency as a plain fixed-window INCR.
            result = await self._rate_limit_script(keys=keys, args=[window_seconds * 2])
            current = int(result[0])
            prev = int(result[1])
        except Exception as e:
            logger.warning(
                "Redis rate limit check failed (%s), fail mode: %s",
                type(e).__name__,
                self._fail_mode,
            )
            self._degraded(window_seconds)
            await self._check_fallback(identifier, limit, window_seconds)
            return

        self._enforce(prev, current, limit, window_seconds, elapsed)


__all__ = ["RateLimiter"]
