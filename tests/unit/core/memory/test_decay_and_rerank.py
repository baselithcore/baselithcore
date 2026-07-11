"""Decay-based pruning wiring + opt-in recall rerank."""

from datetime import UTC, datetime, timedelta

import pytest

from core.memory.hierarchy import HierarchicalMemory, MemoryTier

# ---------------------------------------------------------------------------
# prune_low_relevance (RelevanceCalculator wiring)
# ---------------------------------------------------------------------------


async def test_prune_drops_old_low_relevance_items():
    memory = HierarchicalMemory()
    await memory.add("ancient trivia", tier=MemoryTier.MTM, importance=0.05)
    await memory.add("fresh important fact", tier=MemoryTier.MTM, importance=0.9)
    # Make the first item ancient (past max_age_days=365).
    memory._mtm[0].created_at = datetime.now(UTC) - timedelta(days=400)

    counts = memory.prune_low_relevance()

    assert counts["mtm"] == 1
    assert [i.content for i in memory._mtm] == ["fresh important fact"]
    assert len(memory._mtm_embeddings) == 1


async def test_prune_ltm_preserves_deque_maxlen():
    memory = HierarchicalMemory()
    await memory.add("old ltm", tier=MemoryTier.LTM, importance=0.05)
    await memory.add("new ltm", tier=MemoryTier.LTM, importance=0.9)
    memory._ltm[0].created_at = datetime.now(UTC) - timedelta(days=400)

    counts = memory.prune_low_relevance()

    assert counts["ltm"] == 1
    assert [i.content for i in memory._ltm] == ["new ltm"]
    assert memory._ltm.maxlen == memory.config.ltm.max_items


async def test_fresh_important_items_survive():
    memory = HierarchicalMemory()
    await memory.add("keep me", tier=MemoryTier.MTM, importance=0.9)
    counts = memory.prune_low_relevance()
    assert counts == {"mtm": 0, "ltm": 0}
    assert len(memory._mtm) == 1


async def test_decay_prune_runs_in_sweep_only_when_enabled(monkeypatch):
    from core.memory.hierarchy import HierarchyConfig, TierConfig

    # No TTL on MTM: only decay-prune (not the TTL sweep) may evict here.
    config = HierarchyConfig(mtm=TierConfig(max_items=50, ttl_seconds=None))
    memory = HierarchicalMemory(config=config)
    await memory.add("ancient", tier=MemoryTier.MTM, importance=0.05)
    memory._mtm[0].created_at = datetime.now(UTC) - timedelta(days=400)

    monkeypatch.setenv("BASELITH_MEMORY_DECAY_PRUNE", "false")
    memory._sweep_expired_tiers()
    assert len(memory._mtm) == 1  # default off: sweep does not decay-prune

    monkeypatch.setenv("BASELITH_MEMORY_DECAY_PRUNE", "true")
    memory._sweep_expired_tiers()
    assert len(memory._mtm) == 0


# ---------------------------------------------------------------------------
# Opt-in recall rerank
# ---------------------------------------------------------------------------


class FakeReranker:
    """Scores by keyword overlap: puts the on-topic item first."""

    def predict(self, pairs):
        return [1.0 if pair[0].split()[0] in pair[1] else 0.0 for pair in pairs]


async def _memory_with_embedder(monkeypatch):
    """Deterministic setup: dense order paris-first, rerank prefers tokyo.

    The embedder keys on 'paris'; the query mentions paris (dense winner)
    but STARTS with 'tokyo' (FakeReranker winner) — so a reorder is only
    observable when the reranker actually ran. Hybrid fusion is disabled to
    keep the pre-rerank order purely cosine-driven.
    """
    import core.memory.hierarchy_search as hs

    monkeypatch.setattr(hs, "_HYBRID_RECALL_ENABLED", False)

    class UnitEmbedder:
        async def encode(self, text):
            return [1.0, 0.6] if "paris" in text.lower() else [0.6, 1.0]

    memory = HierarchicalMemory(embedder=UnitEmbedder())
    await memory.add("paris population data")
    await memory.add("tokyo population data")
    return memory


QUERY = "tokyo versus paris"  # embeds paris-like; first token is 'tokyo'


async def test_rerank_off_by_default(monkeypatch):
    monkeypatch.delenv("BASELITH_MEMORY_RERANK", raising=False)
    memory = await _memory_with_embedder(monkeypatch)
    calls = {"n": 0}

    def fake_get_reranker():
        calls["n"] += 1
        return FakeReranker()

    import core.chat.dependencies as deps

    monkeypatch.setattr(deps, "get_reranker", fake_get_reranker)
    items = await memory.recall(QUERY, limit=2)
    assert calls["n"] == 0  # never consulted when the flag is off
    assert items[0].content == "paris population data"  # dense order intact


async def test_rerank_reorders_when_enabled(monkeypatch):
    monkeypatch.setenv("BASELITH_MEMORY_RERANK", "true")
    memory = await _memory_with_embedder(monkeypatch)

    import core.chat.dependencies as deps

    monkeypatch.setattr(deps, "get_reranker", lambda: FakeReranker())
    items = await memory.recall(QUERY, limit=2)
    assert items[0].content == "tokyo population data"  # reranker flipped it
    assert len(items) == 2  # same k, order only


async def test_rerank_fails_open(monkeypatch):
    monkeypatch.setenv("BASELITH_MEMORY_RERANK", "true")
    memory = await _memory_with_embedder(monkeypatch)

    import core.chat.dependencies as deps

    def broken():
        raise RuntimeError("model not installed")

    monkeypatch.setattr(deps, "get_reranker", broken)
    items = await memory.recall(QUERY, limit=2)
    assert len(items) == 2  # dense order returned, no exception
    assert items[0].content == "paris population data"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
