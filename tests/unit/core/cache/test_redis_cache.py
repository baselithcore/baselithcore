"""Tests for Redis cache connection pooling helpers."""

import asyncio
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


def test_pool_carries_socket_deadlines():
    """The shared pool must bound both connect and per-operation socket waits.

    Without them an unresponsive-but-connected Redis hangs the caller forever
    while holding a pooled connection, so hung operations exhaust the pool.
    """
    with (
        patch.object(redis_cache, "ConnectionPool") as mock_connection_pool,
        patch.object(redis_cache, "Redis"),
    ):
        mock_connection_pool.from_url.return_value = MagicMock()
        redis_cache.create_redis_client("redis://localhost:6379/0")

    kwargs = mock_connection_pool.from_url.call_args.kwargs
    assert kwargs["socket_timeout"] > 0
    assert kwargs["socket_connect_timeout"] > 0


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


def test_pools_are_not_shared_across_event_loops():
    """Each event loop gets its own pool.

    ``redis.asyncio`` connections are bound to the loop that opened them. One
    process-global pool let a connection opened on a since-closed loop (an
    ``asyncio.run`` in a worker thread) be handed to the serving loop, where
    every command then failed with ``RuntimeError: Event loop is closed``.
    """
    with (
        patch.object(redis_cache, "ConnectionPool") as mock_connection_pool,
        patch.object(redis_cache, "Redis") as mock_redis,
    ):
        mock_connection_pool.from_url.side_effect = lambda *a, **k: MagicMock()

        async def make() -> None:
            redis_cache.create_redis_client("redis://localhost:6379/0")

        asyncio.run(make())
        asyncio.run(make())

    first, second = (c.kwargs["connection_pool"] for c in mock_redis.call_args_list)
    assert first is not second
    assert mock_connection_pool.from_url.call_count == 2
    # The first loop is closed, so its pool is no longer offered to anyone.
    assert len(redis_cache._shared_pools) == 1


def test_same_loop_reuses_one_pool():
    """Within one loop the pool stays shared (bounded connections, no churn)."""
    with (
        patch.object(redis_cache, "ConnectionPool") as mock_connection_pool,
        patch.object(redis_cache, "Redis"),
    ):
        mock_connection_pool.from_url.return_value = MagicMock()

        async def make_two() -> None:
            redis_cache.create_redis_client("redis://localhost:6379/0")
            redis_cache.create_redis_client("redis://localhost:6379/0")

        asyncio.run(make_two())

    assert mock_connection_pool.from_url.call_count == 1
