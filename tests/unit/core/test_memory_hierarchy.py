"""
Unit Tests for Hierarchical Memory System.

Tests for HierarchicalMemory, tier management, and consolidation.
"""

import pytest


class TestMemoryTierAndConfig:
    """Tests for MemoryTier and configuration classes."""

    def test_tier_enum_values(self):
        """Tier enum has expected values."""
        from core.memory.hierarchy import MemoryTier

        assert MemoryTier.STM.value == "short_term"
        assert MemoryTier.MTM.value == "mid_term"
        assert MemoryTier.LTM.value == "long_term"

    def test_tier_config_defaults(self):
        """TierConfig has sensible defaults."""
        from core.memory.hierarchy import TierConfig

        config = TierConfig(max_items=10)
        assert config.max_items == 10
        assert config.auto_promote_threshold == 0.5
        assert config.ttl_seconds is None

    def test_hierarchy_config_defaults(self):
        """HierarchyConfig has sensible defaults."""
        from core.memory.hierarchy import HierarchyConfig

        config = HierarchyConfig()
        assert config.stm.max_items == 10
        assert config.mtm.max_items == 50
        assert config.ltm.max_items == 500
        assert config.auto_consolidate is True


class TestHierarchicalMemory:
    """Tests for HierarchicalMemory class."""

    def test_initialization(self):
        """Basic initialization with defaults."""
        from core.memory.hierarchy import HierarchicalMemory

        mem = HierarchicalMemory()
        assert mem.config is not None
        assert len(mem._stm) == 0
        assert len(mem._mtm) == 0
        assert len(mem._ltm) == 0

    def test_initialization_with_config(self):
        """Initialization with custom config."""
        from core.memory.hierarchy import (
            HierarchicalMemory,
            HierarchyConfig,
            TierConfig,
        )

        config = HierarchyConfig(
            stm=TierConfig(max_items=5),
            mtm=TierConfig(max_items=20),
        )
        mem = HierarchicalMemory(config=config)

        assert mem.config.stm.max_items == 5
        assert mem.config.mtm.max_items == 20

    @pytest.mark.asyncio
    async def test_add_to_stm(self):
        """Add item to short-term memory."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()
        item = await mem.add("Test content", tier=MemoryTier.STM)

        assert len(mem._stm) == 1
        assert mem._stm[0].content == "Test content"
        assert item.metadata.get("tier") == "short_term"

    @pytest.mark.asyncio
    async def test_add_to_mtm(self):
        """Add item to mid-term memory."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()
        await mem.add("MTM content", tier=MemoryTier.MTM)

        assert len(mem._mtm) == 1
        assert mem._mtm[0].content == "MTM content"

    @pytest.mark.asyncio
    async def test_add_to_ltm(self):
        """Add item to long-term memory."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()
        await mem.add("LTM content", tier=MemoryTier.LTM)

        assert len(mem._ltm) == 1
        assert mem._ltm[0].content == "LTM content"

    @pytest.mark.asyncio
    async def test_stm_fifo_eviction(self):
        """STM evicts oldest when at capacity with auto_consolidate=False."""
        from core.memory.hierarchy import (
            HierarchicalMemory,
            HierarchyConfig,
            TierConfig,
            MemoryTier,
        )

        config = HierarchyConfig(
            stm=TierConfig(max_items=3),
            auto_consolidate=False,  # Simple FIFO for this test
        )
        mem = HierarchicalMemory(config=config)

        await mem.add("Item 1", tier=MemoryTier.STM)
        await mem.add("Item 2", tier=MemoryTier.STM)
        await mem.add("Item 3", tier=MemoryTier.STM)
        await mem.add("Item 4", tier=MemoryTier.STM)  # Triggers eviction

        assert len(mem._stm) == 3
        assert mem._stm[0].content == "Item 2"  # Oldest (Item 1) was evicted

    @pytest.mark.asyncio
    async def test_stm_auto_consolidate(self):
        """STM consolidates to MTM when at capacity."""
        from core.memory.hierarchy import (
            HierarchicalMemory,
            HierarchyConfig,
            TierConfig,
            MemoryTier,
        )

        config = HierarchyConfig(
            stm=TierConfig(max_items=3),
            auto_consolidate=True,
        )
        mem = HierarchicalMemory(config=config)

        await mem.add("Item 1", tier=MemoryTier.STM)
        await mem.add("Item 2", tier=MemoryTier.STM)
        await mem.add("Item 3", tier=MemoryTier.STM)
        await mem.add("Item 4", tier=MemoryTier.STM)  # Triggers consolidation

        # Some items should have moved to MTM
        assert len(mem._mtm) > 0
        assert len(mem._stm) <= 3


class TestHierarchicalMemoryConsolidation:
    """Tests for memory consolidation."""

    @pytest.mark.asyncio
    async def test_consolidate_stm_to_mtm(self):
        """Manual STM to MTM consolidation."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()

        # Add several items to STM
        for i in range(5):
            await mem.add(f"STM item {i}", tier=MemoryTier.STM)

        assert len(mem._stm) == 5
        assert len(mem._mtm) == 0

        # Consolidate
        migrated = await mem.consolidate_stm(items_to_migrate=3)

        assert migrated == 3
        assert len(mem._stm) == 2
        assert len(mem._mtm) == 3

    @pytest.mark.asyncio
    async def test_compress_mtm_to_ltm(self):
        """Compress MTM to LTM."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()

        # Add items to MTM
        for i in range(10):
            await mem.add(f"MTM item {i}", tier=MemoryTier.MTM)

        assert len(mem._mtm) == 10

        # Compress
        compressed = await mem.compress_mtm(target_count=5)

        assert compressed == 5
        assert len(mem._mtm) == 5
        assert len(mem._ltm) == 1  # One summary created


class TestHierarchicalMemoryRecall:
    """Tests for memory recall."""

    @pytest.mark.asyncio
    async def test_recall_from_stm(self):
        """Recall items from STM using keyword search."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()
        await mem.add("The weather is sunny today", tier=MemoryTier.STM)
        await mem.add("User prefers dark mode", tier=MemoryTier.STM)

        results = await mem.recall("weather", tiers=[MemoryTier.STM])

        assert len(results) == 1
        assert "weather" in results[0].content

    @pytest.mark.asyncio
    async def test_recall_across_tiers(self):
        """Recall searches across all specified tiers."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()
        await mem.add("STM weather info", tier=MemoryTier.STM)
        await mem.add("MTM weather history", tier=MemoryTier.MTM)

        results = await mem.recall("weather")

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_recall_limit(self):
        """Recall respects limit parameter."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()
        for i in range(10):
            await mem.add(f"Test memory {i}", tier=MemoryTier.STM)

        results = await mem.recall("Test", limit=3)

        assert len(results) <= 3


class TestHierarchicalMemoryContext:
    """Tests for context generation."""

    @pytest.mark.asyncio
    async def test_get_context_empty(self):
        """Get context from empty memory."""
        from core.memory.hierarchy import HierarchicalMemory

        mem = HierarchicalMemory()
        context = mem.get_context()

        assert context == ""

    @pytest.mark.asyncio
    async def test_get_context_with_stm(self):
        """Get context includes STM items."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()
        await mem.add("Recent event 1", tier=MemoryTier.STM)
        await mem.add("Recent event 2", tier=MemoryTier.STM)

        context = mem.get_context()

        assert "Recent Context" in context
        assert "Recent event 1" in context
        assert "Recent event 2" in context

    @pytest.mark.asyncio
    async def test_get_context_respects_max_tokens(self):
        """Context generation respects token limit."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()
        # Add many items
        for i in range(20):
            await mem.add(
                f"Memory item {i} with some extra content", tier=MemoryTier.STM
            )

        context = mem.get_context(max_tokens=200)

        assert len(context) <= 250  # Some tolerance for headers


class TestHierarchicalMemoryStats:
    """Tests for tier statistics."""

    @pytest.mark.asyncio
    async def test_get_tier_stats_empty(self):
        """Get stats for empty memory."""
        from core.memory.hierarchy import HierarchicalMemory

        mem = HierarchicalMemory()
        stats = mem.get_tier_stats()

        assert len(stats) == 3
        assert all(s.item_count == 0 for s in stats)

    @pytest.mark.asyncio
    async def test_get_tier_stats_with_items(self):
        """Get stats with items in tiers."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()
        await mem.add("STM item", tier=MemoryTier.STM)
        await mem.add("MTM item", tier=MemoryTier.MTM)

        stats = mem.get_tier_stats()
        stats_by_tier = {s.tier: s for s in stats}

        assert stats_by_tier[MemoryTier.STM].item_count == 1
        assert stats_by_tier[MemoryTier.MTM].item_count == 1
        assert stats_by_tier[MemoryTier.LTM].item_count == 0

    @pytest.mark.asyncio
    async def test_clear_all(self):
        """Clear all tiers."""
        from core.memory.hierarchy import HierarchicalMemory, MemoryTier

        mem = HierarchicalMemory()
        await mem.add("STM", tier=MemoryTier.STM)
        await mem.add("MTM", tier=MemoryTier.MTM)
        await mem.add("LTM", tier=MemoryTier.LTM)

        counts = mem.clear_all()

        assert counts["stm"] == 1
        assert counts["mtm"] == 1
        assert counts["ltm"] == 1
        assert len(mem._stm) == 0
        assert len(mem._mtm) == 0
        assert len(mem._ltm) == 0
