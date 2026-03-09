"""
Tests for core.resilience.rate_limiter module.
"""

import time

from core.resilience.rate_limiter import (
    InMemoryRateLimiter,
    RateLimiter,
    RateLimitResult,
    get_api_limiter,
    get_llm_limiter,
)


class TestRateLimitResult:
    """Tests for RateLimitResult dataclass."""

    def test_allowed_result(self):
        """Test allowed result."""
        result = RateLimitResult(
            allowed=True,
            remaining=9,
            reset_at=time.time() + 60,
        )

        assert result.allowed is True
        assert result.remaining == 9
        assert result.retry_after is None

    def test_denied_result(self):
        """Test denied result."""
        result = RateLimitResult(
            allowed=False,
            remaining=0,
            reset_at=time.time() + 60,
            retry_after=30.5,
        )

        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after == 30.5


class TestInMemoryRateLimiter:
    """Tests for InMemoryRateLimiter."""

    def test_allows_within_limit(self):
        """Test requests within limit are allowed."""
        limiter = InMemoryRateLimiter()

        result = limiter.check("user1", limit=10, window=60)

        assert result.allowed is True
        assert result.remaining == 9

    def test_blocks_over_limit(self):
        """Test requests over limit are blocked."""
        limiter = InMemoryRateLimiter()

        # Make 3 requests (limit is 3)
        for _ in range(3):
            limiter.check("user1", limit=3, window=60)

        # 4th should be blocked
        result = limiter.check("user1", limit=3, window=60)

        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after is not None

    def test_different_keys_isolated(self):
        """Test different keys have separate limits."""
        limiter = InMemoryRateLimiter()

        # Use up limit for user1
        for _ in range(3):
            limiter.check("user1", limit=3, window=60)

        # user2 should still be allowed
        result = limiter.check("user2", limit=3, window=60)

        assert result.allowed is True

    def test_cleanup_removes_expired(self):
        """Test cleanup removes old entries."""
        limiter = InMemoryRateLimiter()

        limiter.check("old_user", limit=10, window=60)

        # Force old timestamp
        limiter._buckets["old_user"] = (1, time.time() - 7200)

        removed = limiter.cleanup(max_age=3600)

        assert removed == 1
        assert "old_user" not in limiter._buckets


class TestRateLimiter:
    """Tests for RateLimiter high-level interface."""

    def test_default_init(self):
        """Test default initialization."""
        limiter = RateLimiter()

        assert limiter.limit == 100
        assert limiter.window == 60

    def test_custom_init(self):
        """Test custom initialization."""
        limiter = RateLimiter(limit=50, window=30)

        assert limiter.limit == 50
        assert limiter.window == 30

    def test_check_returns_result(self):
        """Test check returns RateLimitResult."""
        limiter = RateLimiter(limit=10, window=60)

        result = limiter.check("test_key")

        assert isinstance(result, RateLimitResult)
        assert result.allowed is True

    def test_is_allowed_simple(self):
        """Test is_allowed simple check."""
        limiter = RateLimiter(limit=10, window=60)

        assert limiter.is_allowed("test_key") is True


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_get_api_limiter(self):
        """Test API limiter factory."""
        limiter = get_api_limiter(limit=200, window=120)

        assert limiter.limit == 200
        assert limiter.window == 120

    def test_get_llm_limiter(self):
        """Test LLM limiter factory."""
        limiter = get_llm_limiter()

        assert limiter.limit == 20  # Default restrictive
        assert limiter.window == 60
