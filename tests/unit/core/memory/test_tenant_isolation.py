"""In-process memory tiers must not pool tenants together.

``AgentMemory`` and ``HierarchicalMemory`` are routinely process-wide
singletons (``core.memory.get_memory``), yet their tiers were plain instance
lists: whatever one tenant's turn appended, the next tenant's context builder
read straight back out. The tiers are now keyed by the bound tenant.
"""

import asyncio
from contextlib import contextmanager

import pytest

from core import context as core_context
from core.context import (
    TenantContextError,
    reset_tenant_context,
    set_tenant_context,
)
from core.memory.hierarchy import HierarchicalMemory, MemoryTier
from core.memory.manager import AgentMemory


@contextmanager
def as_tenant(tenant_id: str):
    token = set_tenant_context(tenant_id)
    try:
        yield
    finally:
        reset_tenant_context(token)


@contextmanager
def no_tenant():
    """Unbind the tenant contextvar (the autouse fixture binds ``default``)."""
    token = core_context._tenant_context.set(None)
    try:
        yield
    finally:
        core_context._tenant_context.reset(token)


class _Config:
    def __init__(self, strict: bool) -> None:
        self.strict_tenant_isolation = strict


@contextmanager
def strict_isolation(monkeypatch: pytest.MonkeyPatch, enabled: bool):
    monkeypatch.setattr(
        core_context, "get_app_config", lambda: _Config(enabled), raising=True
    )
    yield


async def test_working_memory_is_scoped_per_tenant():
    memory = AgentMemory()

    with as_tenant("acme"):
        await memory.add_memory("acme secret")
        assert memory.working_memory_size == 1

    with as_tenant("globex"):
        assert memory.working_memory_size == 0
        contents = [item.content for item in memory._working_memory]
        assert "acme secret" not in contents

    with as_tenant("acme"):
        assert memory.working_memory_size == 1


async def test_clear_working_memory_only_clears_the_current_tenant():
    memory = AgentMemory()

    with as_tenant("acme"):
        await memory.add_memory("acme item")
    with as_tenant("globex"):
        await memory.add_memory("globex item")
        assert memory.clear_working_memory() == 1

    with as_tenant("acme"):
        assert memory.working_memory_size == 1


async def test_working_memory_embeddings_track_their_tenants_items():
    memory = AgentMemory()

    with as_tenant("acme"):
        await memory.add_memory("acme item")
        assert len(memory._working_memory_embeddings) == 1
    with as_tenant("globex"):
        assert memory._working_memory_embeddings == []


async def test_hierarchy_tiers_are_scoped_per_tenant():
    hierarchy = HierarchicalMemory()

    with as_tenant("acme"):
        await hierarchy.add("acme stm", tier=MemoryTier.STM)
        await hierarchy.add("acme mtm", tier=MemoryTier.MTM)
        await hierarchy.add("acme ltm", tier=MemoryTier.LTM)
        assert hierarchy.clear_all() == {"stm": 1, "mtm": 1, "ltm": 1}

    with as_tenant("acme"):
        await hierarchy.add("acme stm", tier=MemoryTier.STM)

    with as_tenant("globex"):
        assert list(hierarchy._stm) == []
        assert list(hierarchy._mtm) == []
        assert list(hierarchy._ltm) == []
        context = hierarchy.get_context(max_tokens=2000)
        assert "acme stm" not in context


async def test_hierarchy_ltm_stays_a_bounded_deque_per_tenant():
    hierarchy = HierarchicalMemory()

    with as_tenant("acme"):
        assert hierarchy._ltm.maxlen == hierarchy.config.ltm.max_items
    with as_tenant("globex"):
        assert hierarchy._ltm.maxlen == hierarchy.config.ltm.max_items


async def test_tier_stats_are_not_served_across_tenants():
    """The 1s stats cache must not hand one tenant another's counts."""
    hierarchy = HierarchicalMemory()

    with as_tenant("acme"):
        await hierarchy.add("acme stm", tier=MemoryTier.STM)
        acme_counts = {s.tier: s.item_count for s in hierarchy.get_tier_stats()}

    with as_tenant("globex"):
        globex_counts = {s.tier: s.item_count for s in hierarchy.get_tier_stats()}

    assert acme_counts[MemoryTier.STM] == 1
    assert globex_counts[MemoryTier.STM] == 0


async def test_maintenance_single_flight_is_per_tenant():
    """One tenant's in-flight consolidation must not suppress another's."""
    hierarchy = HierarchicalMemory()

    async def _never_finishes() -> None:
        await asyncio.Event().wait()

    with as_tenant("acme"):
        hierarchy._schedule_maintenance("stm_consolidate", _never_finishes)
        hierarchy._schedule_maintenance("stm_consolidate", _never_finishes)
    with as_tenant("globex"):
        hierarchy._schedule_maintenance("stm_consolidate", _never_finishes)

    try:
        names = set(hierarchy._maintenance_tasks)
        assert names == {"stm_consolidate:acme", "stm_consolidate:globex"}
    finally:
        for task in hierarchy._maintenance_tasks.values():
            task.cancel()
        await asyncio.gather(
            *hierarchy._maintenance_tasks.values(), return_exceptions=True
        )


async def test_unbound_tenant_uses_default_when_strict_isolation_is_off(monkeypatch):
    memory = AgentMemory()

    with strict_isolation(monkeypatch, False), no_tenant():
        await memory.add_memory("background job item")

    with as_tenant("default"):
        assert memory.working_memory_size == 1


async def test_unbound_tenant_raises_under_strict_isolation(monkeypatch):
    memory = AgentMemory()
    hierarchy = HierarchicalMemory()

    with strict_isolation(monkeypatch, True), no_tenant():
        with pytest.raises(TenantContextError):
            _ = memory.working_memory_size
        with pytest.raises(TenantContextError):
            hierarchy.clear_all()
