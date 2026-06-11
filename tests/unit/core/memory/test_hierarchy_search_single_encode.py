"""Regression test: hierarchical recall encodes the query exactly once.

STM and MTM searches used to call embedder.encode(query) independently —
the dominant recall cost. recall() now encodes once and shares the vector.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from core.memory.hierarchy import HierarchicalMemory, MemoryTier


def _embedder() -> AsyncMock:
    embedder = AsyncMock()
    embedder.encode = AsyncMock(return_value=[1.0, 0.0, 0.0])
    return embedder


async def test_recall_encodes_query_once_across_tiers() -> None:
    embedder = _embedder()
    memory = HierarchicalMemory(embedder=embedder)
    await memory.add("the quick brown fox", tier=MemoryTier.STM)
    await memory.add("jumped over the lazy dog", tier=MemoryTier.MTM)

    embedder.encode.reset_mock()
    await memory.recall("fox", tiers=[MemoryTier.STM, MemoryTier.MTM])

    # One encode for the query itself, shared by STM and MTM searches.
    assert embedder.encode.call_count == 1


async def test_recall_still_returns_semantic_matches() -> None:
    memory = HierarchicalMemory(embedder=_embedder())
    await memory.add("the quick brown fox", tier=MemoryTier.STM)

    results = await memory.recall("fox", tiers=[MemoryTier.STM])
    assert results
    assert "fox" in results[0].content
