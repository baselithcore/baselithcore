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
from core.config.security import get_security_config
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
            # Amortized: at most one O(n) sweep per 100 checks, and only once
            # the map is large — never on every request under the global lock.
            self._fallback_checks_since_prune += 1
            if len(self._fallback) > 1000 and self._fallback_checks_since_prune >= 100:
                self._fallback_checks_since_prune = 0
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
            self._degraded(window_seconds)
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
                "Redis rate limit check failed (%s), fail mode: %s",
                type(e).__name__,
                self._fail_mode,
            )
            self._degraded(window_seconds)
            await self._check_fallback(identifier, limit, window_seconds)
            return

        if current > limit:
            # A negative TTL (-1 no expiry / -2 missing) collapses to the full
            # window as a safe Retry-After hint.
            reset = ttl if ttl >= 0 else window_seconds
            _raise_rate_limited(_rate_limit_headers(limit, current, reset))


__all__ = ["RateLimiter"]
