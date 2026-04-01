import importlib.util
import numpy as np
import pytest
from pathlib import Path
from unittest.mock import AsyncMock


MODULE_PATH = (
    Path(__file__).resolve().parents[5] / "core/services/vectorstore/embedding_cache.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location("test_embedding_cache_module", MODULE_PATH)
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
