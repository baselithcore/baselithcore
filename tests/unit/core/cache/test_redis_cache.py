"""Tests for Redis cache connection pooling helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.cache import redis_cache


def setup_function() -> None:
    """Reset shared pool registry before each test."""
    redis_cache._shared_pools.clear()


def teardown_function() -> None:
    """Reset shared pool registry after each test."""
    redis_cache._shared_pools.clear()


def test_create_redis_client_reuses_shared_pool():
    """Clients for the same URL should reuse the same connection pool."""
    pool = MagicMock()
    client_one = MagicMock()
    client_two = MagicMock()

    with (
        patch.object(redis_cache, "ConnectionPool") as mock_connection_pool,
        patch.object(redis_cache, "Redis") as mock_redis,
    ):
        mock_connection_pool.from_url.return_value = pool
        mock_redis.side_effect = [client_one, client_two]

        first = redis_cache.create_redis_client("redis://localhost:6379/0")
        second = redis_cache.create_redis_client("redis://localhost:6379/0")

    assert first is client_one
    assert second is client_two
    assert mock_connection_pool.from_url.call_count == 1
    mock_redis.assert_any_call(connection_pool=pool)
    assert mock_redis.call_count == 2


@pytest.mark.asyncio
async def test_close_redis_pools_disconnects_all_shared_pools():
    """Closing pools disconnects every shared connection pool exactly once."""
    pool_one = AsyncMock()
    pool_two = AsyncMock()
    redis_cache._shared_pools["redis://one"] = pool_one
    redis_cache._shared_pools["redis://two"] = pool_two

    await redis_cache.close_redis_pools()

    pool_one.disconnect.assert_awaited_once()
    pool_two.disconnect.assert_awaited_once()
    assert redis_cache._shared_pools == {}
