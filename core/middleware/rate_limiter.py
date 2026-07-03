"""
Distributed rate limiting.

Redis-backed fixed-window rate limiter with an in-memory fallback, used by
``core.middleware.security.SecurityManager`` on every authenticated request.
Extracted from ``core/middleware/security.py`` to keep modules under the
500-line cap; the class is re-exported there for backward compatibility.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import HTTPException, status

from core.cache.redis_cache import create_redis_client
from core.config.cache import get_redis_cache_config
from core.middleware._security_metrics import SECURITY_EVENTS
from core.observability.logging import get_logger

logger = get_logger(__name__)

# Atomic fixed-window counter: INCR + first-call EXPIRE in one round trip.
# Replaces the previous SET NX EX + INCR pair (2 RTT per request) while
# keeping the same TOCTOU-free semantics — the script runs atomically.
# Returns {count, ttl} so the caller can populate Retry-After / RateLimit-Reset
# without a second round trip.
_RATE_LIMIT_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


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


class RateLimiter:
    """
    Distributed rate limiter by role/key/IP, using Redis.
    """

    def __init__(self) -> None:
        cache_config = get_redis_cache_config()
        self._prefix = cache_config.cache_prefix + ":ratelimit:"
        self._redis = None
        self._rate_limit_script: Any = None
        self._fallback: dict[str, tuple[int, float]] = {}
        self._fallback_lock = asyncio.Lock()
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

    async def _check_fallback(
        self, identifier: str, limit: int, window_seconds: int
    ) -> None:
        """Best-effort local fixed-window fallback when Redis is unavailable."""
        async with self._fallback_lock:
            now = time.time()
            count, window_start = self._fallback.get(identifier, (0, now))
            if now - window_start >= window_seconds:
                count = 0
                window_start = now

            count += 1
            self._fallback[identifier] = (count, window_start)

            # Prune expired entries to prevent unbounded memory growth.
            # Only run periodically (every ~100 checks) to avoid O(n) cost on each request.
            if len(self._fallback) > 1000:
                cutoff = now - window_seconds
                self._fallback = {
                    k: v for k, v in self._fallback.items() if v[1] > cutoff
                }

            if count > limit:
                reset = int(window_seconds - (now - window_start))
                _raise_rate_limited(_rate_limit_headers(limit, count, reset))

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

        key = f"{self._prefix}{identifier}"

        if self._redis is None:
            await self._check_fallback(identifier, limit, window_seconds)
            return

        try:
            # Single atomic Lua round trip: INCR + EXPIRE-on-first-hit, then
            # TTL. The script executes atomically server-side, so the TTL is
            # always set together with the first increment (no TOCTOU window)
            # at half the per-request Redis latency of the old SET NX + INCR.
            result = await self._rate_limit_script(keys=[key], args=[window_seconds])
            current = int(result[0])
            ttl = int(result[1])
        except Exception as e:
            logger.warning(
                "Redis rate limit check failed (%s), using in-memory fallback",
                type(e).__name__,
            )
            await self._check_fallback(identifier, limit, window_seconds)
            return

        if current > limit:
            # A negative TTL (-1 no expiry / -2 missing) collapses to the full
            # window as a safe Retry-After hint.
            reset = ttl if ttl >= 0 else window_seconds
            _raise_rate_limited(_rate_limit_headers(limit, current, reset))


__all__ = ["RateLimiter"]
