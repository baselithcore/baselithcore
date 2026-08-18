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


async def test_ltm_reuses_query_vector_when_provider_shares_embedder() -> None:
    """A full-tier recall hands the already-computed query vector to the LTM
    provider (identity-guarded), so LTM skips a redundant second encode."""
    embedder = _embedder()
    provider = AsyncMock()
    provider.embedder = embedder  # same instance → shared-vector fast path
    provider.search = AsyncMock(return_value=[])

    memory = HierarchicalMemory(embedder=embedder, provider=provider)
    await memory.add("the quick brown fox", tier=MemoryTier.STM)

    embedder.encode.reset_mock()
    await memory.recall("fox", tiers=[MemoryTier.STM, MemoryTier.LTM])

    # STM encodes the query once; LTM reuses that vector instead of re-encoding.
    assert embedder.encode.call_count == 1
    assert provider.search.await_args.kwargs["query_vector"] == [1.0, 0.0, 0.0]


async def test_ltm_provider_reencodes_when_embedder_differs() -> None:
    """If the provider embeds with a different instance, the shared vector is
    withheld (no cross-space mix) and the provider re-encodes from text."""
    embedder = _embedder()
    provider = AsyncMock()
    provider.embedder = _embedder()  # different instance
    provider.search = AsyncMock(return_value=[])

    memory = HierarchicalMemory(embedder=embedder, provider=provider)
    await memory.add("the quick brown fox", tier=MemoryTier.STM)

    await memory.recall("fox", tiers=[MemoryTier.STM, MemoryTier.LTM])

    assert provider.search.await_args.kwargs["query_vector"] is None
