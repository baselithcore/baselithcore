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
from typing import Any, Optional

from fastapi import HTTPException, status

from core.cache.redis_cache import create_redis_client
from core.config.cache import get_redis_cache_config
from core.middleware._security_metrics import SECURITY_EVENTS
from core.observability.logging import get_logger

logger = get_logger(__name__)

# Atomic fixed-window counter: INCR + first-call EXPIRE in one round trip.
# Replaces the previous SET NX EX + INCR pair (2 RTT per request) while
# keeping the same TOCTOU-free semantics — the script runs atomically.
_RATE_LIMIT_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


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
                SECURITY_EVENTS.labels(reason="rate_limited").inc()
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded, please try again shortly.",
                )

    async def check(
        self, identifier: str, limit: Optional[int], window_seconds: int
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
            # Single atomic Lua round trip: INCR + EXPIRE-on-first-hit. The
            # script executes atomically server-side, so the TTL is always
            # set together with the first increment (no TOCTOU window) at
            # half the per-request Redis latency of the old SET NX + INCR.
            current = int(
                await self._rate_limit_script(keys=[key], args=[window_seconds])
            )
        except Exception as e:
            logger.warning(
                "Redis rate limit check failed (%s), using in-memory fallback",
                type(e).__name__,
            )
            await self._check_fallback(identifier, limit, window_seconds)
            return

        if current > limit:
            SECURITY_EVENTS.labels(reason="rate_limited").inc()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded, please try again shortly.",
            )


__all__ = ["RateLimiter"]
