import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[5] / "core/services/vectorstore/embedding_cache.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location(
    "test_embedding_cache_module", MODULE_PATH
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
MODULE = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(MODULE)
get_embeddings_cached = MODULE.get_embeddings_cached


class BatchCache:
    def __init__(self, cached_values):
        self._cached_values = cached_values
        self.get_many_mock = AsyncMock(return_value=cached_values)
        self.set_many_mock = AsyncMock()
        self.get = AsyncMock()
        self.set = AsyncMock()

    async def get_many(self, keys):
        return await self.get_many_mock(keys)

    async def set_many(self, items):
        await self.set_many_mock(items)


class AsyncEmbedder:
    def __init__(self, vectors):
        self.encode = AsyncMock(return_value=vectors)


@pytest.mark.asyncio
async def test_get_embeddings_cached_uses_batch_cache_and_async_embedder():
    cache = BatchCache(cached_values=[[0.9, 0.9], None])
    embedder = AsyncEmbedder(np.array([[0.1, 0.2]]))

    vectors = await get_embeddings_cached(
        embedder, ["cached", "missing"], cache, model_id="test-model"
    )

    assert vectors == [[0.9, 0.9], [0.1, 0.2]]
    cache.get_many_mock.assert_called_once()
    cache.set_many_mock.assert_called_once()
    cache.get.assert_not_called()
    cache.set.assert_not_called()
    embedder.encode.assert_called_once_with(["missing"], convert_to_numpy=True)


class DictCache:
    """Shared in-memory stand-in for the Redis embedding cache."""

    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl=None):
        self.store[key] = value


class PromptedModel:
    """A model with SentenceTransformer-style prompts (remote or local)."""

    def __init__(self, document_prefix, marker):
        self.prompts = {"query": "", "document": document_prefix}
        self.marker = marker
        self.encoded = []

    def encode(self, sentences, **_):
        self.encoded.extend(sentences)
        return [[self.marker] for _ in sentences]


@pytest.mark.asyncio
async def test_changed_document_prefix_does_not_serve_stale_vectors():
    cache = DictCache()
    old = PromptedModel("", marker=1.0)
    new = PromptedModel("passage: ", marker=2.0)

    await get_embeddings_cached(old, ["chunk"], cache, model_id="e5")
    vectors = await get_embeddings_cached(new, ["chunk"], cache, model_id="e5")

    assert new.encoded == ["chunk"], "a vector cached under the old prefix was reused"
    assert vectors == [[2.0]]


@pytest.mark.asyncio
async def test_prefix_is_read_through_a_cached_embedder_wrapper():
    cache = DictCache()

    class Wrapper:
        def __init__(self, model):
            self.model = model

        def encode(self, sentences, **kwargs):
            return self.model.encode(sentences, **kwargs)

    await get_embeddings_cached(
        Wrapper(PromptedModel("", 1.0)), ["chunk"], cache, model_id="e5"
    )
    new = PromptedModel("passage: ", 2.0)
    await get_embeddings_cached(Wrapper(new), ["chunk"], cache, model_id="e5")
    assert new.encoded == ["chunk"]


@pytest.mark.asyncio
async def test_dimension_is_part_of_the_key():
    cache = DictCache()
    first = PromptedModel("", 1.0)
    second = PromptedModel("", 2.0)

    await get_embeddings_cached(first, ["chunk"], cache, model_id="m", dim=384)
    await get_embeddings_cached(second, ["chunk"], cache, model_id="m", dim=768)
    assert second.encoded == ["chunk"]


@pytest.mark.asyncio
async def test_same_model_prefix_and_dim_still_hit_the_cache():
    cache = DictCache()
    first = PromptedModel("passage: ", 1.0)
    second = PromptedModel("passage: ", 2.0)

    await get_embeddings_cached(first, ["chunk"], cache, model_id="m", dim=384)
    vectors = await get_embeddings_cached(
        second, ["chunk"], cache, model_id="m", dim=384
    )
    assert second.encoded == []
    assert vectors == [[1.0]]
