"""
Unit tests for TTLCache optimizations (Throttled Purge).
"""

import pytest
import time
from unittest.mock import patch
from core.cache import TTLCache


class TestTTLCacheOptimization:
    """Tests for TTLCache optimizations."""

    @pytest.mark.asyncio
    async def test_basic_get_set(self):
        """Test basic get/set functionality still works."""
        cache = TTLCache(maxsize=10, ttl=60)
        await cache.set("key", "value")
        assert await cache.get("key") == "value"
        assert await cache.get("missing") is None

    @pytest.mark.asyncio
    async def test_expiration_consistency(self):
        """Test that expired items are NOT returned even if purge hasn't run."""
        cache = TTLCache(maxsize=10, ttl=1)
        await cache.set("key", "value")

        # Immediate access
        assert await cache.get("key") == "value"

        # Wait for expiration
        time.sleep(1.1)

        # Should return None because it's expired, regardless of purge
        assert await cache.get("key") is None

    @pytest.mark.asyncio
    async def test_purge_throttling(self):
        """Test that _purge_expired is not called on every operation."""
        cache = TTLCache(maxsize=10, ttl=60)

        # Use wraps so the real method runs and updates _last_purge_time
        with patch.object(
            cache, "_purge_expired", wraps=cache._purge_expired
        ) as spied_purge:
            # First call should trigger purge (since initialized at 0.0)
            await cache.set("key1", "value1")
            assert spied_purge.call_count == 1

            # Immediate subsequent call should skip purge (throttled)
            await cache.set("key2", "value2")
            assert spied_purge.call_count == 1  # Should still be 1

    @pytest.mark.asyncio
    async def test_purge_runs_after_interval(self):
        """Test that purge runs after interval passes."""
        cache = TTLCache(maxsize=10, ttl=60)

        # We need to access internal _last_purge_time or mock time
        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            cache._last_purge_time = 1000.0  # Pretend we just purged

            # Operation within interval
            mock_time.return_value = 1010.0  # +10s
            with patch.object(
                cache, "_purge_expired", wraps=cache._purge_expired
            ) as spied_purge:
                await cache.set("k", "v")
                spied_purge.assert_not_called()

            # Operation after interval
            mock_time.return_value = 1070.0  # +70s (>60s)
            with patch.object(
                cache, "_purge_expired", wraps=cache._purge_expired
            ) as spied_purge:
                await cache.set("k2", "v2")
                spied_purge.assert_called_once()
