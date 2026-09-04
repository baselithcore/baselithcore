"""The synchronous Redis clients share one bounded pool per settings tuple.

Before this, every sync call site built its own client with ``Redis.from_url``
and inherited redis-py's defaults: an effectively unlimited connection count
and, worse, no socket deadline — a Redis that accepted the connection and then
stopped answering blocked the calling thread forever.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.cache import redis_sync


@pytest.fixture(autouse=True)
def _clean_registry():
    redis_sync._sync_pools.clear()
    yield
    redis_sync._sync_pools.clear()


@pytest.fixture
def fake_redis():
    with (
        patch.object(redis_sync, "ConnectionPool") as pool_cls,
        patch.object(redis_sync, "Redis") as client_cls,
    ):
        pool_cls.from_url.side_effect = lambda *a, **kw: MagicMock(name="pool")
        yield pool_cls, client_cls


def test_pool_is_bounded_and_has_socket_deadlines(fake_redis):
    pool_cls, _ = fake_redis
    redis_sync.create_sync_redis_client("redis://x:6379/0")

    kwargs = pool_cls.from_url.call_args.kwargs
    assert kwargs["max_connections"] > 0
    assert kwargs["socket_timeout"] is not None
    assert kwargs["socket_connect_timeout"] is not None
    assert kwargs["health_check_interval"] is not None


def test_same_settings_reuse_one_pool(fake_redis):
    pool_cls, client_cls = fake_redis
    redis_sync.create_sync_redis_client("redis://x:6379/0")
    redis_sync.create_sync_redis_client("redis://x:6379/0")

    assert pool_cls.from_url.call_count == 1
    assert client_cls.call_count == 2
    first, second = (c.kwargs["connection_pool"] for c in client_cls.call_args_list)
    assert first is second


@pytest.mark.parametrize(
    "second_call",
    [
        {"url": "redis://other:6379/0"},
        {"url": "redis://x:6379/0", "decode_responses": True},
        {"url": "redis://x:6379/0", "socket_timeout": 99.0},
    ],
)
def test_settings_that_differ_never_share_a_pool(fake_redis, second_call):
    """``decode_responses`` and the deadline are connection-level in redis-py:
    sharing a pool would silently hand a caller the other one's settings."""
    pool_cls, _ = fake_redis
    redis_sync.create_sync_redis_client("redis://x:6379/0")
    url = second_call.pop("url")
    redis_sync.create_sync_redis_client(url, **second_call)

    assert pool_cls.from_url.call_count == 2


def test_explicit_socket_timeout_overrides_the_config_default(fake_redis):
    pool_cls, _ = fake_redis
    redis_sync.create_sync_redis_client("redis://x:6379/0", socket_timeout=1.5)

    assert pool_cls.from_url.call_args.kwargs["socket_timeout"] == 1.5


def test_close_disconnects_and_forgets_every_pool(fake_redis):
    pool_cls, _ = fake_redis
    redis_sync.create_sync_redis_client("redis://a:6379/0")
    redis_sync.create_sync_redis_client("redis://b:6379/0")
    pools = list(redis_sync._sync_pools.values())

    redis_sync.close_sync_redis_pools()

    assert redis_sync._sync_pools == {}
    for pool in pools:
        pool.disconnect.assert_called_once()


def test_close_survives_a_pool_that_raises(fake_redis):
    """Shutdown must not be derailed by an already-broken connection."""
    redis_sync.create_sync_redis_client("redis://a:6379/0")
    next(iter(redis_sync._sync_pools.values())).disconnect.side_effect = OSError("gone")

    redis_sync.close_sync_redis_pools()
    assert redis_sync._sync_pools == {}


def test_missing_redis_package_is_a_clear_error():
    with patch.object(redis_sync, "Redis", None):
        with pytest.raises(RuntimeError, match="redis package"):
            redis_sync.create_sync_redis_client("redis://x:6379/0")


def test_close_is_a_noop_without_the_redis_package():
    with patch.object(redis_sync, "ConnectionPool", None):
        redis_sync.close_sync_redis_pools()
