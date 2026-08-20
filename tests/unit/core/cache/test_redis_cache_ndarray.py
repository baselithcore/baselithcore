"""Embedding payloads (numpy arrays) must survive the Redis cache serializer.

`CachedEmbedder._store_embeddings` hands raw `np.ndarray` values to
`RedisTTLCache.set/set_many`. The orjson fallback used to call `float(obj)`
on anything with `__float__` — which a multi-element ndarray has, but raises
`TypeError` for — so with `CACHE_BACKEND=redis` every cache write blew up and
the embedding cache never got a single hit.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from core.cache.redis_cache import RedisTTLCache, _json_default


def _cache() -> RedisTTLCache:
    return RedisTTLCache(MagicMock(), prefix="test", default_ttl=60)


def test_json_default_serializes_multielement_ndarray() -> None:
    arr = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    out = _json_default(arr)
    assert out == pytest.approx([0.1, 0.2, 0.3])


def test_json_default_still_handles_scalars() -> None:
    assert _json_default(np.float32(1.5)) == pytest.approx(1.5)
    assert _json_default(np.int64(7)) == 7


def test_ndarray_value_roundtrips_through_serializer() -> None:
    cache = _cache()
    arr = np.random.default_rng(0).random(384).astype(np.float32)
    data = cache._serialize_value(arr)
    restored = cache._deserialize_value(data)
    assert restored == pytest.approx(arr.tolist())


async def test_set_with_ndarray_does_not_raise() -> None:
    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    client.setex = AsyncMock(return_value=True)
    cache = RedisTTLCache(client, prefix="test", default_ttl=60)
    await cache.set("h", np.zeros(8, dtype=np.float32))
    assert client.set.await_count + client.setex.await_count == 1


async def test_set_many_with_ndarrays_does_not_raise() -> None:
    client = MagicMock()
    pipe = MagicMock()
    pipe.setex = MagicMock()
    pipe.execute = AsyncMock(return_value=[True, True])
    client.pipeline = MagicMock(return_value=pipe)
    cache = RedisTTLCache(client, prefix="test", default_ttl=60)
    await cache.set_many(
        [("h1", np.zeros(4, dtype=np.float32)), ("h2", np.ones(4, dtype=np.float32))]
    )
    assert pipe.execute.await_count == 1
    assert pipe.setex.call_count == 2
