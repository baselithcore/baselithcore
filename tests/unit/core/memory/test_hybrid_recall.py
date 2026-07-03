"""Tests for hybrid (BM25 + dense RRF) memory recall wiring."""

from unittest.mock import AsyncMock, patch

from core.memory.hierarchy import HierarchicalMemory, MemoryTier

_FLAG = "core.memory.hierarchy_search._HYBRID_RECALL_ENABLED"


def _orthogonal_embedder():
    # item vector, then query vector: orthogonal → cosine 0 (< 0.5 threshold).
    emb = AsyncMock()
    emb.encode = AsyncMock(side_effect=[[0.0, 1.0], [1.0, 0.0]])
    return emb


async def test_bm25_rescues_keyword_hit_below_cosine_threshold():
    mem = HierarchicalMemory(embedder=_orthogonal_embedder())
    await mem.add("error code E123 occurred", tier=MemoryTier.STM)

    with patch(_FLAG, True):
        results = await mem.recall("E123", tiers=[MemoryTier.STM])

    # Cosine is 0 here; only the BM25 keyword pass can surface this item.
    assert results
    assert "E123" in results[0].content


async def test_pure_cosine_misses_keyword_hit_when_hybrid_disabled():
    mem = HierarchicalMemory(embedder=_orthogonal_embedder())
    await mem.add("error code E123 occurred", tier=MemoryTier.STM)

    with patch(_FLAG, False):
        results = await mem.recall("E123", tiers=[MemoryTier.STM])

    # Dense-only: cosine 0 < 0.5 threshold → nothing recalled.
    assert results == []


async def test_hybrid_dedups_duplicate_content_across_tiers():
    mem = HierarchicalMemory()  # no embedder → keyword/BM25 path
    await mem.add("shared note about weather", tier=MemoryTier.STM)
    await mem.add("shared note about weather", tier=MemoryTier.MTM)

    with patch(_FLAG, True):
        results = await mem.recall("weather")

    # Same content in two tiers collapses to a single hit.
    assert len(results) == 1


async def test_hybrid_encodes_query_once():
    emb = AsyncMock()
    emb.encode = AsyncMock(return_value=[1.0, 0.0, 0.0])
    mem = HierarchicalMemory(embedder=emb)
    await mem.add("the quick brown fox", tier=MemoryTier.STM)
    await mem.add("jumped over the lazy dog", tier=MemoryTier.MTM)

    emb.encode.reset_mock()
    with patch(_FLAG, True):
        await mem.recall("fox", tiers=[MemoryTier.STM, MemoryTier.MTM])

    # BM25 needs no embeddings → the single-encode optimization is preserved.
    assert emb.encode.call_count == 1
