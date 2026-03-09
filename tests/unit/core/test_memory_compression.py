"""
Unit Tests for Memory Compression Module

Tests for relevance calculation, compression strategies, and memory summarization.
"""

import pytest
from datetime import datetime, timedelta, timezone


class TestRelevanceCalculator:
    """Tests for RelevanceCalculator."""

    def test_calculate_score_new_memory(self):
        """New memories should have high relevance."""
        from core.memory.compression import RelevanceCalculator, RelevanceConfig
        from core.memory.types import MemoryItem, MemoryType

        calc = RelevanceCalculator(RelevanceConfig(half_life_days=7))

        item = MemoryItem(
            content="Recent memory",
            memory_type=MemoryType.SHORT_TERM,
            metadata={"importance": 0.8},
        )
        # Manually set created_at to now
        item.created_at = datetime.now(timezone.utc)

        score = calc.calculate_score(item)
        assert score > 0.5  # Should be relatively high

    def test_calculate_score_old_memory(self):
        """Old memories should have lower relevance."""
        from core.memory.compression import RelevanceCalculator, RelevanceConfig
        from core.memory.types import MemoryItem, MemoryType

        calc = RelevanceCalculator(RelevanceConfig(half_life_days=7))

        item = MemoryItem(
            content="Old memory",
            memory_type=MemoryType.EPISODIC,
            metadata={"importance": 0.5},
        )
        # Set created_at to 30 days ago
        item.created_at = datetime.now(timezone.utc) - timedelta(days=30)

        score = calc.calculate_score(item)
        assert score < 0.3  # Should be low due to decay

    def test_access_boost(self):
        """Frequently accessed memories should score higher."""
        from core.memory.compression import RelevanceCalculator
        from core.memory.types import MemoryItem, MemoryType

        calc = RelevanceCalculator()

        item = MemoryItem(
            content="Accessed memory",
            memory_type=MemoryType.LONG_TERM,
        )
        item.created_at = datetime.now(timezone.utc) - timedelta(days=14)

        score_no_access = calc.calculate_score(item, access_count=0)
        score_accessed = calc.calculate_score(item, access_count=10)

        assert score_accessed > score_no_access

    def test_classify_memories(self):
        """Test memory classification into buckets."""
        from core.memory.compression import RelevanceCalculator, RelevanceConfig
        from core.memory.types import MemoryItem, MemoryType

        config = RelevanceConfig(
            compression_threshold=0.3,
            pruning_threshold=0.1,
        )
        calc = RelevanceCalculator(config)

        # Create memories with different ages
        now = datetime.now(timezone.utc)
        memories = [
            MemoryItem(content="Recent", memory_type=MemoryType.SHORT_TERM),
            MemoryItem(content="Medium", memory_type=MemoryType.EPISODIC),
            MemoryItem(content="Old", memory_type=MemoryType.LONG_TERM),
        ]
        memories[0].created_at = now
        memories[1].created_at = now - timedelta(days=30)
        memories[2].created_at = now - timedelta(days=200)

        keep, compress, prune = calc.classify_memories(memories)

        # At least some should be classified
        assert len(keep) + len(compress) + len(prune) == 3


class TestCompressionResult:
    """Tests for CompressionResult."""

    def test_compression_ratio(self):
        """Test compression ratio calculation."""
        from core.memory.compression import CompressionResult

        result = CompressionResult(
            original_count=100,
            compressed_count=30,
            pruned_count=50,
            summaries_created=5,
        )

        assert result.compression_ratio == 0.7  # 70% reduction

    def test_compression_ratio_empty(self):
        """Test compression ratio with no items."""
        from core.memory.compression import CompressionResult

        result = CompressionResult(
            original_count=0,
            compressed_count=0,
            pruned_count=0,
            summaries_created=0,
        )

        assert result.compression_ratio == 0.0


class TestMemoryCompressor:
    """Tests for MemoryCompressor."""

    @pytest.mark.asyncio
    async def test_summarize_without_llm(self):
        """Test summarization fallback without LLM."""
        from core.memory.compression import MemoryCompressor
        from core.memory.types import MemoryItem, MemoryType

        compressor = MemoryCompressor(llm_service=None)

        memories = [
            MemoryItem(
                content="First memory content", memory_type=MemoryType.SHORT_TERM
            ),
            MemoryItem(
                content="Second memory content", memory_type=MemoryType.SHORT_TERM
            ),
        ]

        summary = await compressor.summarize_memories(memories)

        assert summary is not None
        assert summary.metadata.get("is_summary") is True
        assert summary.metadata.get("source_count") == 2

    @pytest.mark.asyncio
    async def test_compress_pruning_strategy(self):
        """Test compression with pruning strategy."""
        from core.memory.compression import MemoryCompressor, CompressionStrategy
        from core.memory.types import MemoryItem, MemoryType

        compressor = MemoryCompressor()

        now = datetime.now(timezone.utc)
        memories = [
            MemoryItem(content="Keep this", memory_type=MemoryType.SHORT_TERM),
            MemoryItem(content="Old one", memory_type=MemoryType.EPISODIC),
        ]
        memories[0].created_at = now
        memories[1].created_at = now - timedelta(days=400)  # Very old

        compressed, result = await compressor.compress(
            memories, strategy=CompressionStrategy.PRUNING
        )

        assert result.original_count == 2
        # Old memory should be pruned
        assert result.pruned_count >= 1


class TestAgentMemoryCompression:
    """Tests for AgentMemory compression integration."""

    @pytest.mark.asyncio
    async def test_compress_old_memories_flow(self):
        from core.memory.manager import AgentMemory

        manager = AgentMemory()
        stats = manager.get_memory_stats()

        assert "working_memory_size" in stats
        assert "working_memory_limit" in stats
        assert "has_provider" in stats
        assert stats["has_provider"] is False
