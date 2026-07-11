"""Tests for memory tier lifecycle policies (core.memory.lifecycle)."""

from datetime import UTC, datetime, timedelta

import pytest

from core.memory.hierarchy import (
    HierarchicalMemory,
    HierarchyConfig,
    MemoryTier,
    TierConfig,
)
from core.memory.lifecycle import (
    drop_duplicates,
    is_expired,
    partition_expired,
    select_promotable,
    ttl_enforcement_enabled,
)
from core.memory.types import MemoryItem, MemoryType


def _item(content: str, importance: float = 0.5, age_seconds: float = 0.0):
    item = MemoryItem(
        content=content,
        memory_type=MemoryType.SHORT_TERM,
        metadata={"importance": importance},
    )
    if age_seconds:
        item.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return item


# ---------------------------------------------------------------------------
# Pure policies
# ---------------------------------------------------------------------------


def test_select_promotable_default_importance_promotes_everything():
    candidates = [(_item(f"m{i}"), []) for i in range(3)]
    promote, evicted = select_promotable(candidates, threshold=0.5)
    assert len(promote) == 3
    assert evicted == 0


def test_select_promotable_evicts_below_threshold():
    keep = _item("important", importance=0.9)
    drop = _item("trivia", importance=0.1)
    promote, evicted = select_promotable([(keep, []), (drop, [])], threshold=0.5)
    assert [i.content for i, _ in promote] == ["important"]
    assert evicted == 1


def test_drop_duplicates_against_target_tier_and_within_batch():
    existing = [_item("User prefers dark mode")]
    candidates = [
        (_item("user  prefers DARK mode"), []),  # dup of existing (normalized)
        (_item("new fact"), [0.1]),
        (_item("New Fact"), []),  # in-batch dup
    ]
    unique, dropped = drop_duplicates(candidates, existing)
    assert [i.content for i, _ in unique] == ["new fact"]
    assert dropped == 2


def test_is_expired_none_ttl_never_expires():
    assert is_expired(_item("x", age_seconds=10**9), None) is False


def test_partition_expired_filters_items_and_embeddings():
    fresh = _item("fresh")
    stale = _item("stale", age_seconds=120)
    alive, alive_emb, expired = partition_expired(
        [stale, fresh], [[0.1], [0.2]], ttl_seconds=60
    )
    assert [i.content for i in alive] == ["fresh"]
    assert alive_emb == [[0.2]]
    assert expired == 1


def test_partition_expired_no_ttl_passthrough():
    items = [_item("a")]
    alive, alive_emb, expired = partition_expired(items, None, None)
    assert alive is items
    assert alive_emb == []
    assert expired == 0


def test_ttl_enforcement_env_toggle(monkeypatch):
    monkeypatch.setenv("BASELITH_MEMORY_TTL_ENFORCE", "false")
    assert ttl_enforcement_enabled() is False
    monkeypatch.setenv("BASELITH_MEMORY_TTL_ENFORCE", "true")
    assert ttl_enforcement_enabled() is True


# ---------------------------------------------------------------------------
# HierarchicalMemory integration
# ---------------------------------------------------------------------------


def _memory(**tier_overrides):
    config = HierarchyConfig(
        stm=tier_overrides.get("stm", TierConfig(max_items=10)),
        mtm=tier_overrides.get("mtm", TierConfig(max_items=50)),
        ltm=tier_overrides.get("ltm", TierConfig(max_items=500)),
    )
    return HierarchicalMemory(config=config)


async def test_consolidate_promotes_only_above_threshold():
    memory = _memory(stm=TierConfig(max_items=10, auto_promote_threshold=0.5))
    await memory.add("keep me", importance=0.8)
    await memory.add("drop me", importance=0.2)

    migrated = await memory.consolidate_stm(items_to_migrate=2)

    assert migrated == 1
    assert [i.content for i in memory._mtm] == ["keep me"]
    assert memory._stm == []  # the window still rolled for both


async def test_consolidate_drops_duplicates_already_in_mtm():
    memory = _memory()
    await memory.add("Known fact", tier=MemoryTier.MTM)
    await memory.add("known  FACT")  # STM, dup after normalization
    await memory.add("fresh fact")

    migrated = await memory.consolidate_stm(items_to_migrate=2)

    assert migrated == 1
    contents = [i.content for i in memory._mtm]
    assert contents == ["Known fact", "fresh fact"]


async def test_default_importance_consolidation_unchanged():
    """Legacy behavior preserved: default-importance items all promote."""
    memory = _memory()
    for i in range(5):
        await memory.add(f"item {i}")
    migrated = await memory.consolidate_stm(items_to_migrate=5)
    assert migrated == 5
    assert len(memory._mtm) == 5


async def test_purge_expired_sweeps_mtm_and_ltm():
    memory = _memory(
        mtm=TierConfig(max_items=50, ttl_seconds=60),
        ltm=TierConfig(max_items=500, ttl_seconds=60),
    )
    await memory.add("old mtm", tier=MemoryTier.MTM)
    await memory.add("new mtm", tier=MemoryTier.MTM)
    await memory.add("old ltm", tier=MemoryTier.LTM)
    memory._mtm[0].created_at = datetime.now(UTC) - timedelta(seconds=120)
    memory._ltm[0].created_at = datetime.now(UTC) - timedelta(seconds=120)

    counts = memory.purge_expired()

    assert counts == {"stm": 0, "mtm": 1, "ltm": 1}
    assert [i.content for i in memory._mtm] == ["new mtm"]
    assert len(memory._mtm_embeddings) == 1
    assert list(memory._ltm) == []


async def test_purge_expired_disabled_via_env(monkeypatch):
    monkeypatch.setenv("BASELITH_MEMORY_TTL_ENFORCE", "false")
    memory = _memory(mtm=TierConfig(max_items=50, ttl_seconds=60))
    await memory.add("old mtm", tier=MemoryTier.MTM)
    memory._mtm[0].created_at = datetime.now(UTC) - timedelta(seconds=120)

    counts = memory.purge_expired()

    assert counts == {"stm": 0, "mtm": 0, "ltm": 0}
    assert len(memory._mtm) == 1


async def test_consolidate_sweeps_expired_mtm_first():
    memory = _memory(mtm=TierConfig(max_items=50, ttl_seconds=60))
    await memory.add("expired mtm", tier=MemoryTier.MTM)
    memory._mtm[0].created_at = datetime.now(UTC) - timedelta(seconds=120)
    await memory.add("stm item")

    migrated = await memory.consolidate_stm(items_to_migrate=1)

    assert migrated == 1
    assert [i.content for i in memory._mtm] == ["stm item"]


async def test_ltm_deque_maxlen_preserved_after_sweep():
    memory = _memory(ltm=TierConfig(max_items=3, ttl_seconds=60))
    for i in range(3):
        await memory.add(f"ltm {i}", tier=MemoryTier.LTM)
    memory._ltm[0].created_at = datetime.now(UTC) - timedelta(seconds=120)

    memory.purge_expired()
    assert memory._ltm.maxlen == 3
    # Capacity eviction still works after the rebuild.
    for i in range(5):
        await memory.add(f"more {i}", tier=MemoryTier.LTM)
    assert len(memory._ltm) == 3


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
