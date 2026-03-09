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
