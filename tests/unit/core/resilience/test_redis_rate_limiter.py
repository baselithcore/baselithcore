"""Tests for the Redis-backed rate limiter and its in-memory fallback.

The Redis client is mocked; the registered Lua script is replaced by a
callable so the result mapping and every fallback path can be asserted.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from baselith.core.resilience import rate_limiter
from baselith.core.resilience.rate_limiter import (
    InMemoryRateLimiter,
    RateLimiter,
    RedisRateLimiter,
    get_llm_limiter,
)

REDIS_SYNC = "baselith.core.cache.redis_sync"


@pytest.fixture
def redis_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch the sync client factory; returns the client it hands out."""
    client = MagicMock()
    client.script = MagicMock(return_value=[1, 4, 0])
    client.register_script.return_value = client.script
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(f"{REDIS_SYNC}.create_sync_redis_client", factory)
    client.factory = factory
    return client


class TestInMemoryWindow:
    def test_window_rollover_resets_the_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [100.0]
        monkeypatch.setattr(rate_limiter, "time", SimpleNamespace(time=lambda: now[0]))
        limiter = InMemoryRateLimiter()
        assert limiter.check("k", limit=1, window=10).allowed
        denied = limiter.check("k", limit=1, window=10)
        assert not denied.allowed
        assert denied.retry_after == pytest.approx(10.0)
        now[0] += 11
        fresh = limiter.check("k", limit=1, window=10)
        assert fresh.allowed
        assert fresh.reset_at == pytest.approx(121.0)


class TestRedisRateLimiterInit:
    def test_connects_and_registers_script(self, redis_client: MagicMock) -> None:
        limiter = RedisRateLimiter("redis://cache:6379/1")
        redis_client.factory.assert_called_once_with(
            "redis://cache:6379/1", decode_responses=True
        )
        redis_client.ping.assert_called_once()
        redis_client.register_script.assert_called_once_with(
            RedisRateLimiter._LUA_SCRIPT
        )
        assert limiter._script is redis_client.script

    def test_url_defaults_to_cache_config(
        self, redis_client: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "baselith.core.config.cache.get_redis_cache_config",
            lambda: SimpleNamespace(url="redis://from-config:6379/0"),
        )
        RedisRateLimiter()
        assert redis_client.factory.call_args.args == ("redis://from-config:6379/0",)

    def test_unreachable_redis_falls_back(self, redis_client: MagicMock) -> None:
        redis_client.ping.side_effect = ConnectionError("refused")
        limiter = RedisRateLimiter("redis://down:6379/0")
        assert limiter._client is None
        # The fallback still enforces the limit, per process.
        assert limiter.check("ip", limit=1, window=60).allowed
        assert not limiter.check("ip", limit=1, window=60).allowed
        redis_client.script.assert_not_called()

    def test_missing_redis_package_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, REDIS_SYNC, None)  # import -> ImportError
        limiter = RedisRateLimiter("redis://x:6379/0")
        assert limiter._client is None
        assert limiter.check("ip", limit=2, window=60).remaining == 1


class TestRedisRateLimiterCheck:
    def test_allowed_result_maps_script_reply(self, redis_client: MagicMock) -> None:
        limiter = RedisRateLimiter("redis://r:6379/0")
        result = limiter.check("user-1", limit=5, window=30)
        assert result.allowed is True
        assert result.remaining == 4
        assert result.retry_after is None
        kwargs = redis_client.script.call_args.kwargs
        assert kwargs["keys"] == ["ratelimit:user-1"]
        now, window, limit, request_id = kwargs["args"]
        assert (window, limit) == (30, 5)
        assert result.reset_at == pytest.approx(now + 30)
        assert request_id.endswith(":1")

    def test_request_ids_are_unique_per_call(self, redis_client: MagicMock) -> None:
        limiter = RedisRateLimiter("redis://r:6379/0")
        limiter.check("k", limit=5, window=30)
        limiter.check("k", limit=5, window=30)
        ids = [c.kwargs["args"][3] for c in redis_client.script.call_args_list]
        assert len(set(ids)) == 2

    def test_denied_result_carries_retry_after(self, redis_client: MagicMock) -> None:
        redis_client.script.return_value = [0, 0, "12.5"]
        result = RedisRateLimiter("redis://r:6379/0").check("k", limit=1, window=60)
        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after == 12.5

    def test_script_failure_uses_in_memory_fallback(
        self, redis_client: MagicMock
    ) -> None:
        redis_client.script.side_effect = TimeoutError("slow redis")
        limiter = RedisRateLimiter("redis://r:6379/0")
        first = limiter.check("k", limit=1, window=60)
        second = limiter.check("k", limit=1, window=60)
        assert (first.allowed, second.allowed) == (True, False)

    def test_plugs_into_rate_limiter_facade(self, redis_client: MagicMock) -> None:
        redis_client.script.return_value = [0, 0, 3]
        limiter = get_llm_limiter(
            limit=2, window=10, backend=RedisRateLimiter("redis://r:6379/0")
        )
        assert isinstance(limiter, RateLimiter)
        assert limiter.is_allowed("model-call") is False
        assert redis_client.script.call_args.kwargs["args"][1:3] == [10, 2]
