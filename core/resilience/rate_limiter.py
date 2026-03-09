"""
Rate limiting for API protection.

Provides in-memory and Redis-backed rate limiting.
"""

from core.observability.logging import get_logger
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = get_logger(__name__)


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    remaining: int
    reset_at: float
    retry_after: Optional[float] = None


class RateLimiterBackend(ABC):
    """Abstract rate limiter backend."""

    @abstractmethod
    def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        """
        Check if request is allowed.

        Args:
            key: Unique identifier (e.g., IP, user_id)
            limit: Max requests per window
            window: Window size in seconds

        Returns:
            RateLimitResult with allowed status
        """
        pass


class InMemoryRateLimiter(RateLimiterBackend):
    """
    In-memory rate limiter using sliding window.

    Note: Not suitable for distributed deployments.
    """

    def __init__(self):
        """Initialize in-memory rate limiter state."""
        # key -> (request_count, window_start)
        self._buckets: Dict[str, Tuple[int, float]] = {}

    def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        """
        Check if request is allowed using a sliding window.

        Args:
            key: Unique identifier.
            limit: Maximum allowed requests.
            window: Time window in seconds.

        Returns:
            RateLimitResult containing the evaluation.
        """
        now = time.time()
        window_start = now - window

        if key in self._buckets:
            count, bucket_start = self._buckets[key]

            # Reset if window expired
            if bucket_start < window_start:
                count = 0
                bucket_start = now
        else:
            count = 0
            bucket_start = now

        # Check limit
        if count >= limit:
            retry_after = bucket_start + window - now
            return RateLimitResult(
                allowed=False,
                remaining=0,
                reset_at=bucket_start + window,
                retry_after=retry_after,
            )

        # Increment
        self._buckets[key] = (count + 1, bucket_start)

        return RateLimitResult(
            allowed=True,
            remaining=limit - count - 1,
            reset_at=bucket_start + window,
        )

    def cleanup(self, max_age: int = 3600) -> int:
        """
        Remove expired entries.

        Args:
            max_age: Max age in seconds

        Returns:
            Number of entries removed
        """
        now = time.time()
        cutoff = now - max_age

        expired = [key for key, (_, start) in self._buckets.items() if start < cutoff]

        for key in expired:
            del self._buckets[key]

        return len(expired)


class RedisRateLimiter(RateLimiterBackend):
    """
    Redis-backed rate limiter using sliding window with Lua scripting.

    Uses a sorted set per key where members are unique request timestamps
    and scores are the timestamps. A Lua script atomically:
    1. Removes expired entries outside the window
    2. Counts current entries
    3. Adds the new request if under limit
    4. Sets TTL on the key

    Falls back to InMemoryRateLimiter if Redis is unavailable.
    """

    # Lua script for atomic sliding window rate limiting
    _LUA_SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local request_id = ARGV[4]
    local window_start = now - window

    -- Remove expired entries
    redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

    -- Count current entries in window
    local count = redis.call('ZCARD', key)

    if count < limit then
        -- Add new request
        redis.call('ZADD', key, now, request_id)
        -- Set expiry on the key
        redis.call('EXPIRE', key, window + 1)
        return {1, limit - count - 1, 0}
    else
        -- Get oldest entry to calculate retry_after
        local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        local retry_after = 0
        if #oldest >= 2 then
            retry_after = tonumber(oldest[2]) + window - now
        end
        return {0, 0, retry_after}
    end
    """

    def __init__(self, redis_url: Optional[str] = None):
        """
        Initialize the Redis rate limiter.

        Args:
            redis_url: The connection string for the Redis instance.
        """
        self.redis_url = redis_url
        self._client: Any = None
        self._script: Any = None
        self._fallback = InMemoryRateLimiter()
        self._request_counter = 0

        self._init_client()

    def _init_client(self) -> None:
        """Initialize Redis client with graceful fallback."""
        try:
            from redis import Redis

            url = self.redis_url
            if not url:
                from core.config.cache import get_redis_cache_config

                url = get_redis_cache_config().url

            self._client = Redis.from_url(url, decode_responses=True)
            self._client.ping()
            self._script = self._client.register_script(self._LUA_SCRIPT)
            logger.info(f"RedisRateLimiter connected to {url}")
        except ImportError:
            logger.warning(
                "redis package not installed, falling back to InMemoryRateLimiter"
            )
            self._client = None
        except Exception as e:
            logger.warning(
                f"Redis connection failed ({e}), falling back to InMemoryRateLimiter"
            )
            self._client = None

    def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        """Check rate limit using Redis sliding window."""
        if self._client is None or self._script is None:
            return self._fallback.check(key, limit, window)

        try:
            now = time.time()
            self._request_counter += 1
            request_id = f"{now}:{self._request_counter}"
            redis_key = f"ratelimit:{key}"

            result = self._script(
                keys=[redis_key],
                args=[now, window, limit, request_id],
            )

            allowed = bool(result[0])
            remaining = int(result[1])
            retry_after = float(result[2]) if not allowed else None

            return RateLimitResult(
                allowed=allowed,
                remaining=remaining,
                reset_at=now + window,
                retry_after=retry_after,
            )
        except Exception as e:
            logger.warning(f"Redis rate limit check failed ({e}), using fallback")
            return self._fallback.check(key, limit, window)


class RateLimiter:
    """
    Rate limiter with configurable limits.

    Usage:
        limiter = RateLimiter(limit=100, window=60)  # 100 req/min

        # In request handler
        result = limiter.check(request.client.host)
        if not result.allowed:
            raise HTTPException(429, headers={
                "Retry-After": str(int(result.retry_after))
            })
    """

    def __init__(
        self,
        limit: Optional[int] = None,
        window: Optional[int] = None,
        backend: Optional[RateLimiterBackend] = None,
    ):
        """
        Initialize rate limiter.

        Args:
            limit: Max requests per window
            window: Window size in seconds
            backend: Backend implementation (default: in-memory)
        """
        """
        Initialize rate limiter.

        Args:
            limit: Max requests per window
            window: Window size in seconds
            backend: Backend implementation (default: in-memory)
        """
        from core.config.resilience import get_resilience_config

        config = get_resilience_config()

        self.limit = limit if limit is not None else config.api_rate_limit
        self.window = window if window is not None else config.api_rate_window
        self._backend = backend or InMemoryRateLimiter()

    def check(self, key: str) -> RateLimitResult:
        """
        Check if request is allowed.

        Args:
            key: Unique identifier (IP, user_id, API key)

        Returns:
            RateLimitResult object
        """
        """
        Check if request is allowed.

        Args:
            key: Unique identifier (IP, user_id, API key)

        Returns:
            RateLimitResult
        """
        return self._backend.check(key, self.limit, self.window)

    def is_allowed(self, key: str) -> bool:
        """Simple check returning boolean."""
        return self.check(key).allowed


# Pre-configured limiters
def get_api_limiter(
    limit: Optional[int] = None, window: Optional[int] = None
) -> RateLimiter:
    """Get rate limiter for API endpoints."""
    from core.config import get_resilience_config

    config = get_resilience_config()
    return RateLimiter(
        limit=limit if limit is not None else config.api_rate_limit,
        window=window if window is not None else config.api_rate_window,
    )


def get_llm_limiter(
    limit: Optional[int] = None, window: Optional[int] = None
) -> RateLimiter:
    """Get rate limiter for LLM calls (more restrictive)."""
    from core.config import get_resilience_config

    config = get_resilience_config()
    return RateLimiter(
        limit=limit if limit is not None else config.llm_rate_limit,
        window=window if window is not None else config.llm_rate_window,
    )
