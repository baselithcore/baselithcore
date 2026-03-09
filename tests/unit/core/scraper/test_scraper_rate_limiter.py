"""
Unit tests for RateLimiterMiddleware optimizations.
"""

import pytest
import time
import asyncio
from core.scraper.middleware.rate_limiter import RateLimiterMiddleware


@pytest.mark.asyncio
class TestRateLimiterMiddleware:
    """Tests for RateLimiterMiddleware."""

    async def test_token_acquisition(self):
        """Test basic token acquisition and replenishment."""
        # 10 requests per 1 second
        limiter = RateLimiterMiddleware(requests_per_period=10, period_seconds=1.0)
        key = "example.com"

        # Should start full
        assert limiter._buckets[key] == 10

        await limiter._acquire_token(key)
        assert limiter._buckets[key] == 9

    async def test_blocking_when_empty(self):
        """Test that it blocks when bucket is empty."""
        limiter = RateLimiterMiddleware(requests_per_period=1, period_seconds=0.1)
        key = "example.com"

        await limiter._acquire_token(key)
        assert limiter._buckets[key] == 0

        start = time.monotonic()
        await limiter._acquire_token(key)  # Should wait ~0.1s
        duration = time.monotonic() - start

        assert duration >= 0.09  # Allow small buffer

    async def test_cleanup_logic(self):
        """Test that old buckets are cleaned up."""
        limiter = RateLimiterMiddleware(requests_per_period=10, period_seconds=60)
        limiter.CLEANUP_INTERVAL = 0.1  # Small interval for testing
        limiter.BUCKET_TTL = 0.5  # Small TTL for testing

        # Add some entries
        limiter._buckets["active"] = 10
        limiter._last_update["active"] = time.monotonic()

        limiter._buckets["old"] = 10
        limiter._last_update["old"] = time.monotonic() - 1.0  # Expired

        # Force a cleanup check by acquiring a token
        # We need to wait > CLEANUP_INTERVAL
        await asyncio.sleep(0.2)

        await limiter._acquire_token("active")

        # "old" should be gone, "active" should remain
        assert "old" not in limiter._buckets
        assert "active" in limiter._buckets
