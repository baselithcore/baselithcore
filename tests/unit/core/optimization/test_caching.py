"""
Unit tests for optimization caching.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.optimization.caching import (
    RedisCache,
    SemanticCache,
    SemanticCacheVectorBacked,
)


@pytest.fixture
def mock_redis():
    with patch("core.optimization.caching.redis") as mock_redis_module:
        mock_client = AsyncMock()
        mock_redis_module.from_url.return_value = mock_client
        yield mock_client


@pytest.fixture
def redis_cache(mock_redis):
    cache = RedisCache()
    return cache


@pytest.mark.asyncio
async def test_redis_cache_get_set(redis_cache, mock_redis):
    # Test set
    await redis_cache.set("key", "value", ttl=60)
    mock_redis.set.assert_called_once()

    # Test get string
    mock_redis.get.return_value = "value"
    val = await redis_cache.get("key")
    assert val == "value"

    # Test get json
    mock_redis.get.return_value = '{"a": 1}'
    val = await redis_cache.get("key_json")
    assert val == {"a": 1}


@pytest.mark.asyncio
async def test_redis_cache_get_many(redis_cache, mock_redis):
    # MGET returns values aligned with the requested keys; JSON is decoded,
    # plain strings pass through, and misses become None.
    mock_redis.mget.return_value = ['{"a": 1}', "plain", None]

    values = await redis_cache.get_many(["k1", "k2", "k3"])

    assert values == [{"a": 1}, "plain", None]
    mock_redis.mget.assert_called_once()
    # All keys are namespaced/tenant-scoped before hitting Redis.
    full_keys = mock_redis.mget.call_args.args[0]
    assert len(full_keys) == 3
    assert all(k.endswith(suffix) for k, suffix in zip(full_keys, ["k1", "k2", "k3"]))

    # Empty input short-circuits without touching Redis.
    mock_redis.mget.reset_mock()
    assert await redis_cache.get_many([]) == []
    mock_redis.mget.assert_not_called()


@pytest.mark.asyncio
async def test_redis_cache_set_many(redis_cache, mock_redis):
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    # redis-py's .pipeline() is a sync factory returning a Pipeline object.
    mock_redis.pipeline = MagicMock(return_value=pipe)

    # Accepts a mapping and applies the per-key TTL to every SET.
    await redis_cache.set_many({"k1": {"a": 1}, "k2": "v2"}, ttl=120)

    mock_redis.pipeline.assert_called_once_with(transaction=False)
    assert pipe.set.call_count == 2
    for call in pipe.set.call_args_list:
        assert call.kwargs.get("ex") == 120
    pipe.execute.assert_awaited_once()
    # JSON-serializes complex values, leaves strings as-is.
    serialized = [call.args[1] for call in pipe.set.call_args_list]
    assert '{"a": 1}' in serialized
    assert "v2" in serialized


@pytest.mark.asyncio
async def test_redis_cache_set_many_accepts_tuples(redis_cache, mock_redis):
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    mock_redis.pipeline = MagicMock(return_value=pipe)

    # Also accepts an iterable of (key, value) pairs (used by embedding cache).
    await redis_cache.set_many([("k1", [1, 2]), ("k2", [3, 4])])

    assert pipe.set.call_count == 2
    pipe.execute.assert_awaited_once()

    # Empty input is a no-op.
    mock_redis.pipeline.reset_mock()
    await redis_cache.set_many([])
    mock_redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_semantic_cache(mock_redis):
    cache = SemanticCache()
    # Mock internal cache
    cache.cache = AsyncMock()

    # Test cache response
    await cache.cache_response("prompt", "response")
    cache.cache.set.assert_called_once()

    # Test get response
    cache.cache.get.return_value = "response"
    val = await cache.get_response("prompt")
    assert val == "response"


@pytest.mark.asyncio
async def test_semantic_cache_vector_backed(mock_redis):
    embedder = MagicMock()
    embedder.encode.return_value = [0.1, 0.2]

    cache = SemanticCacheVectorBacked(embedder=embedder)
    cache.cache = AsyncMock()  # Mock redis cache

    # Mock vector service
    mock_vector_service = AsyncMock()
    with patch(
        "core.services.vectorstore.service.get_vectorstore_service",
        return_value=mock_vector_service,
    ):
        # Access vector service to trigger lazy load
        vs = cache.vector_service
        assert vs == mock_vector_service

        # Test cache response with embedding
        await cache.cache_response("prompt", "response")
        cache.cache.set.assert_called_once()  # Redis set
        mock_vector_service.provider.upsert.assert_called_once()  # Vector upsert

        # Test get response semantic (miss)
        mock_vector_service.search.return_value = []
        val = await cache.get_response("prompt")
        assert val is None

        # Test get response semantic (hit)
        mock_result = MagicMock()
        mock_result.score = 0.95
        mock_result.document.metadata = {"prompt_hash": "hash123"}
        mock_vector_service.search.return_value = [mock_result]

        cache.cache.get.return_value = "cached_response"
        val = await cache.get_response("prompt")
        assert val == "cached_response"
