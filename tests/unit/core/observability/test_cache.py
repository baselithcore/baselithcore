import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from core.observability.cache import MemoryCache, Cache, RedisCache


@pytest.mark.asyncio
async def test_memory_cache_operations():
    cache = MemoryCache()

    # Test Set/Get
    await cache.set("key", "value")
    assert await cache.get("key") == "value"
    assert await cache.exists("key") is True

    # Test Delete
    await cache.delete("key")
    assert await cache.get("key") is None
    assert await cache.exists("key") is False


@pytest.mark.asyncio
async def test_memory_cache_ttl():
    cache = MemoryCache()
    await cache.set("key", "value", ttl=1)
    assert await cache.get("key") == "value"

    # Wait for expiration (mock time would be better but sleep is simple here)
    await asyncio.sleep(1.1)
    assert await cache.get("key") is None


@pytest.mark.asyncio
async def test_cache_decorator():
    backend = MemoryCache()
    cache = Cache(backend)

    call_count = 0

    @cache.cached(ttl=60)
    async def expensive_func(x):
        nonlocal call_count
        call_count += 1
        return x * 2

    # First call
    val1 = await expensive_func(10)
    assert val1 == 20
    assert call_count == 1

    # Second call (should be cached)
    val2 = await expensive_func(10)
    assert val2 == 20
    assert call_count == 1

    # Different arg
    val3 = await expensive_func(20)
    assert val3 == 40
    assert call_count == 2


@pytest.mark.asyncio
async def test_redis_cache_mock():
    with patch("redis.asyncio.from_url") as mock_redis:
        mock_client = AsyncMock()
        mock_redis.return_value = mock_client
        mock_client.get.return_value = b'"cached_value"'
        mock_client.exists.return_value = 1

        redis_cache = RedisCache(url="redis://mock")

        # Test get (which lazy inits client)
        val = await redis_cache.get("key")
        assert val == "cached_value"
        mock_client.get.assert_called_with("baselithcore:key")
