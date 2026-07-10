import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from core.cache.semantic_cache import SemanticLLMCache


class TestSemanticLLMCache:
    @pytest.fixture
    def mock_embedder(self):
        with patch("core.nlp.get_embedder") as mock_get:
            embedder = MagicMock()
            # Default behavior: return a normalized vector
            embedder.encode.return_value = np.array([1.0, 0.0, 0.0])
            mock_get.return_value = embedder
            yield embedder

    @pytest.fixture
    def cache(self, mock_embedder):
        return SemanticLLMCache(maxsize=10, ttl=60, threshold=0.8)

    def test_init(self, cache):
        assert cache._maxsize == 10
        assert cache._ttl == 60
        assert cache._threshold == 0.8
        assert len(cache) == 0

    @pytest.mark.asyncio
    async def test_set_and_get_exact(self, cache, mock_embedder):
        # Setup
        prompt = "test prompt"
        response = "test response"
        mock_embedder.encode.return_value = np.array([0.1, 0.2, 0.3])

        # Test Set
        await cache.set(prompt, response)
        assert len(cache) == 1

        # Test Get Exact
        cached = await cache.get_exact(prompt)
        assert cached == response
        assert cache.stats["hits"] == 1

        # Test Get Exact Miss
        assert await cache.get_exact("missing") is None

    @pytest.mark.asyncio
    async def test_get_similar_hit(self, cache, mock_embedder):
        # Setup: cache an entry with vector [1, 0, 0]
        prompt1 = "hello world"
        response1 = "greeting"
        vec1 = np.array([1.0, 0.0, 0.0])
        mock_embedder.encode.return_value = vec1
        await cache.set(prompt1, response1)

        # Setup: query with similar vector [0.9, 0.1, 0.0] (high cosine sim)
        prompt2 = "hello there"
        vec2 = np.array([0.9, 0.1, 0.0])  # dot product ~0.9

        mock_embedder.encode.side_effect = [vec2]

        # Act
        result = await cache.get_similar(prompt2)

        # Assert
        assert result == response1
        assert cache.stats["hits"] == 1

    @pytest.mark.asyncio
    async def test_get_similar_miss_threshold(self, cache, mock_embedder):
        # Setup: cache an entry with vector [1, 0, 0]
        prompt1 = "hello world"
        response1 = "greeting"
        vec1 = np.array([1.0, 0.0, 0.0])
        mock_embedder.encode.return_value = vec1
        await cache.set(prompt1, response1)

        # Setup: query with orthogonal vector [0, 1, 0] (sim=0)
        prompt2 = "totally different"
        vec2 = np.array([0.0, 1.0, 0.0])
        mock_embedder.encode.side_effect = [vec2]

        # Act
        result = await cache.get_similar(prompt2)

        # Assert
        assert result is None
        assert cache.stats["misses"] == 1

    @pytest.mark.asyncio
    async def test_get_similar_empty(self, cache):
        result = await cache.get_similar("anything")
        assert result is None
        assert cache.stats["misses"] == 1

    @pytest.mark.asyncio
    async def test_eviction(self, mock_embedder):
        cache = SemanticLLMCache(maxsize=2, ttl=60)
        mock_embedder.encode.return_value = np.array([1.0, 0.0, 0.0])

        await cache.set("p1", "r1")
        await cache.set("p2", "r2")
        assert len(cache) == 2

        # Should evict LRU (p1)
        await cache.set("p3", "r3")
        assert len(cache) == 2
        assert await cache.get_exact("p1") is None
        assert await cache.get_exact("p2") == "r2"
        assert await cache.get_exact("p3") == "r3"

    @pytest.mark.asyncio
    async def test_expiration(self, mock_embedder):
        cache = SemanticLLMCache(maxsize=10, ttl=0.1)
        mock_embedder.encode.return_value = np.array([1.0, 0.0, 0.0])

        await cache.set("p1", "r1")
        assert await cache.get_exact("p1") == "r1"

        await asyncio.sleep(0.2)
        assert await cache.get_exact("p1") is None
        assert len(cache) == 0

    @pytest.mark.asyncio
    async def test_delete(self, cache, mock_embedder):
        await cache.set("p1", "r1")
        assert len(cache) == 1
        await cache.delete("p1")
        assert len(cache) == 0
        assert await cache.get_exact("p1") is None

    @pytest.mark.asyncio
    async def test_clear(self, cache, mock_embedder):
        await cache.set("p1", "r1")
        await cache.set("p2", "r2")
        assert len(cache) == 2
        await cache.clear()
        assert len(cache) == 0
        assert await cache.get_exact("p1") is None

    @pytest.mark.asyncio
    async def test_matrix_cache_reuse_and_invalidation(self, cache, mock_embedder):
        mock_embedder.encode.return_value = np.array([1.0, 0.0, 0.0])
        await cache.set("p1", "r1")

        # First lookup builds and stores the stacked matrix.
        mock_embedder.encode.side_effect = [np.array([0.9, 0.1, 0.0])]
        assert await cache.get_similar("first query") == "r1"
        tenant = next(iter(cache._matrix_cache))
        matrix = cache._matrix_cache[tenant][1]
        assert matrix.shape[0] == 1

        # Second lookup reuses the same matrix object (no rebuild).
        mock_embedder.encode.side_effect = [np.array([0.9, 0.1, 0.0])]
        assert await cache.get_similar("second query") == "r1"
        assert cache._matrix_cache[tenant][1] is matrix

        # A write invalidates; the next lookup rebuilds with the new row.
        mock_embedder.encode.side_effect = None
        mock_embedder.encode.return_value = np.array([0.0, 1.0, 0.0])
        await cache.set("p2", "r2")
        assert tenant not in cache._matrix_cache

        mock_embedder.encode.side_effect = [np.array([0.9, 0.1, 0.0])]
        assert await cache.get_similar("third query") == "r1"
        assert cache._matrix_cache[tenant][1].shape[0] == 2

    @pytest.mark.asyncio
    async def test_purge_is_interval_gated_but_reads_stay_exact(self, mock_embedder):
        """The full expiry sweep runs at most once per interval, yet an expired
        entry is never served: reads carry their own timestamp check."""
        cache = SemanticLLMCache(maxsize=10, ttl=0.05)
        mock_embedder.encode.return_value = np.array([1.0, 0.0, 0.0])

        await cache.set("p1", "r1")
        await asyncio.sleep(0.08)

        # The set() above ran the sweep; within the 60s interval a get must
        # NOT run another full sweep — but exactness holds regardless.
        assert await cache.get_exact("p1") is None
        assert len(cache) == 0

    @pytest.mark.asyncio
    async def test_get_similar_never_serves_expired_entry(self, mock_embedder):
        cache = SemanticLLMCache(maxsize=10, ttl=0.05, threshold=0.5)
        mock_embedder.encode.return_value = np.array([1.0, 0.0, 0.0])
        await cache.set("p1", "r1")

        # Force a stale matrix snapshot: entry expires, sweep gated away.
        await asyncio.sleep(0.08)
        mock_embedder.encode.side_effect = [np.array([1.0, 0.0, 0.0])]
        result, score = await cache.get_similar_with_score("p1 alike")
        assert result is None

    @pytest.mark.asyncio
    async def test_forced_purge_still_available(self, mock_embedder):
        cache = SemanticLLMCache(maxsize=10, ttl=0.05)
        mock_embedder.encode.return_value = np.array([1.0, 0.0, 0.0])
        await cache.set("p1", "r1")
        await asyncio.sleep(0.08)
        async with cache._lock:
            cache._purge_expired(next(iter(cache._entries)), force=True)
        assert len(cache) == 0
