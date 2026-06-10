"""Regression tests for cache-layer performance optimizations.

Locks two behaviors:
- Redis cache keys (now orjson-serialized) stay deterministic regardless of
  dict insertion order.
- The semantic cache memoizes query embeddings, so a repeated prompt does not
  re-run sentence-transformer inference.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from core.cache.redis_cache import RedisTTLCache
from core.cache.semantic_cache import SemanticLLMCache


class TestRedisKeySerialization:
    @pytest.fixture
    def cache(self, monkeypatch: pytest.MonkeyPatch) -> RedisTTLCache:
        import core.config.cache as cache_config_module

        monkeypatch.setattr(cache_config_module, "_redis_cache_config", None)
        return RedisTTLCache(client=AsyncMock(), prefix="t", default_ttl=60)

    def test_key_is_order_independent(self, cache: RedisTTLCache) -> None:
        k1 = cache._serialize_key({"b": 2, "a": 1, "nested": {"y": 2, "x": 1}})
        k2 = cache._serialize_key({"a": 1, "nested": {"x": 1, "y": 2}, "b": 2})
        assert k1 == k2

    def test_key_distinguishes_different_payloads(self, cache: RedisTTLCache) -> None:
        assert cache._serialize_key({"a": 1}) != cache._serialize_key({"a": 2})

    def test_key_handles_non_str_keys_and_custom_types(
        self, cache: RedisTTLCache
    ) -> None:
        # int keys + a type that goes through the _json_default fallback
        class Score:
            def __float__(self) -> float:
                return 0.5

        key = cache._serialize_key({1: "x", "score": Score()})
        assert key.startswith("t:")

    def test_value_roundtrip_unchanged(self, cache: RedisTTLCache) -> None:
        value = {"a": [1, 2, 3], "b": "text"}
        assert cache._deserialize_value(cache._serialize_value(value)) == value


class TestSemanticCacheEmbeddingMemo:
    @pytest.fixture
    def cache_with_mock_embedder(self) -> tuple[SemanticLLMCache, MagicMock]:
        embedder = MagicMock()
        embedder.encode = MagicMock(
            side_effect=lambda text, convert_to_numpy=True: np.ones(8)
        )
        cache = SemanticLLMCache(maxsize=10, ttl=60, threshold=0.9, embedder=embedder)
        return cache, embedder

    async def test_repeated_prompt_encodes_once(
        self, cache_with_mock_embedder: tuple[SemanticLLMCache, MagicMock]
    ) -> None:
        cache, embedder = cache_with_mock_embedder
        for _ in range(5):
            await cache._compute_embedding("same prompt")
        assert embedder.encode.call_count == 1

    async def test_distinct_prompts_encode_separately(
        self, cache_with_mock_embedder: tuple[SemanticLLMCache, MagicMock]
    ) -> None:
        cache, embedder = cache_with_mock_embedder
        await cache._compute_embedding("prompt one")
        await cache._compute_embedding("prompt two")
        assert embedder.encode.call_count == 2

    async def test_memo_is_bounded(
        self, cache_with_mock_embedder: tuple[SemanticLLMCache, MagicMock]
    ) -> None:
        cache, _ = cache_with_mock_embedder
        for i in range(cache._EMBEDDING_MEMO_MAX + 50):
            await cache._compute_embedding(f"prompt {i}")
        assert len(cache._embedding_memo) <= cache._EMBEDDING_MEMO_MAX


class TestVectorizedSimilarityScan:
    @pytest.fixture
    def cache(self) -> SemanticLLMCache:
        embedder = MagicMock()
        # Distinct, deterministic embeddings per prompt keyword.
        vectors = {
            "alpha": np.array([1.0, 0.0, 0.0]),
            "beta": np.array([0.0, 1.0, 0.0]),
            "alpha-ish": np.array([0.9, 0.1, 0.0]) / np.linalg.norm([0.9, 0.1, 0.0]),
        }
        embedder.encode = MagicMock(
            side_effect=lambda text, convert_to_numpy=True: vectors[text]
        )
        return SemanticLLMCache(maxsize=10, ttl=60, threshold=0.85, embedder=embedder)

    async def test_returns_best_match_above_threshold(
        self, cache: SemanticLLMCache
    ) -> None:
        await cache.set("alpha", "response-alpha")
        await cache.set("beta", "response-beta")

        response, score = await cache.get_similar_with_score("alpha-ish")
        assert response == "response-alpha"
        assert score >= 0.85

    async def test_returns_miss_below_threshold(self, cache: SemanticLLMCache) -> None:
        await cache.set("alpha", "response-alpha")

        response, score = await cache.get_similar_with_score("beta")
        assert response is None
        assert score == 0.0
