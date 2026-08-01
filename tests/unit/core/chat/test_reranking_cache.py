"""Rerank score-cache is read in one batched ``get_many``, not N serial gets."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from core.chat.reranking import rerank_hits


class _BatchCache:
    """Records get/get_many calls; seeded with pre-cached scores by key."""

    def __init__(self, seeded: dict[Any, float]) -> None:
        self._store = dict(seeded)
        self.get_calls = 0
        self.get_many_calls = 0
        self.writes: dict[Any, float] = {}

    async def get(self, key: Any) -> Any:
        self.get_calls += 1
        return self._store.get(key)

    async def get_many(self, keys: Any) -> list[Any]:
        self.get_many_calls += 1
        return [self._store.get(k) for k in keys]

    async def set(self, key: Any, value: Any) -> None:
        self.writes[key] = value


class _Reranker:
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs: list[tuple[str, str]]) -> Any:
        self.calls.append(list(pairs))
        # Deterministic score: length of the chunk text.
        return np.array([float(len(chunk)) for _, chunk in pairs])


def _hit(hit_id: str, text: str) -> Any:
    # A fingerprint is required for _build_cache_key to produce a key.
    return SimpleNamespace(
        id=hit_id,
        payload={
            "text": text,
            "fingerprint": f"fp-{hit_id}",
            "document_id": hit_id,
            "chunk_index": 0,
        },
    )


def _key_for(hit: Any) -> tuple[str, str, str, str] | None:
    from core.chat.reranking import _build_cache_key

    return _build_cache_key("q-norm", hit.payload, hit.id)


@pytest.mark.asyncio
async def test_rerank_reads_cache_in_one_batch():
    hits = [_hit(f"h{i}", f"chunk-{i}") for i in range(5)]
    cache = _BatchCache({})
    reranker = _Reranker()

    ranked = await rerank_hits(
        user_query="query",
        normalized_query="q-norm",
        hits=hits,
        reranker=reranker,
        cache=cache,
    )

    # Exactly one batched read, never a per-hit get.
    assert cache.get_many_calls == 1
    assert cache.get_calls == 0
    # All five were uncached → one predict batch of five, all written back.
    assert len(reranker.calls) == 1
    assert len(reranker.calls[0]) == 5
    assert len(cache.writes) == 5
    assert len(ranked) == 5


@pytest.mark.asyncio
async def test_rerank_uses_cached_scores_and_only_predicts_misses():
    hits = [_hit(f"h{i}", f"chunk-{i}") for i in range(4)]
    reranker = _Reranker()
    seed = {_key_for(hits[0]): 99.0, _key_for(hits[2]): 88.0}
    cache = _BatchCache(seed)

    ranked = await rerank_hits(
        user_query="query",
        normalized_query="q-norm",
        hits=hits,
        reranker=reranker,
        cache=cache,
    )

    assert cache.get_many_calls == 1
    # Only the two uncached hits reach the reranker.
    assert len(reranker.calls) == 1
    assert len(reranker.calls[0]) == 2
    # Cached scores dominate the ranking (99 and 88 are the highest).
    top_scores = [score for _, score in ranked[:2]]
    assert top_scores == [99.0, 88.0]
