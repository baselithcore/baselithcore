"""
Optimization Mixin for AgentMemory.

This mixin manages the lifecycle of memories beyond simple storage.
It provides high-level operations for consolidating working memory into
long-term storage, compressing old memories via summarization, and
graceful 'forgetting' (deletion).
"""

from core.observability.logging import get_logger
from typing import Any, List, Optional, TYPE_CHECKING

from core.memory.types import MemoryItem, MemoryType

if TYPE_CHECKING:
    from core.memory.compression import CompressionResult

logger = get_logger(__name__)


class OptimizationMixin:
    """
    Extends AgentMemory with maintenance operations.

    Ensures the memory system remains performant by preventing context
    bloat and enabling automated archiving/consolidation.
    """

    provider: Optional[Any]
    embedder: Optional[Any]
    _working_memory: List[MemoryItem]
    _working_memory_embeddings: List[List[float]]

    async def consolidate(self) -> None:
        """Merge fragmented memories into more cohesive summaries."""
        if not self.provider:
            return

        for item in self._working_memory:
            item.memory_type = MemoryType.EPISODIC
            await self.provider.add(item)

        logger.info("Memory consolidation complete")

    async def compress_old_memories(
        self,
        days_threshold: int = 7,
        strategy: str = "summarization",
        batch_limit: int = 500,
    ) -> Optional["CompressionResult"]:
        """Archive or summarze older memories to reclaim space.

        Args:
            days_threshold: Age threshold for compression (unused at this layer,
                may be applied by provider/compressor).
            strategy: Compression strategy name.
            batch_limit: Max memories to fetch per compression run. Keeps the
                operation bounded to avoid tenant-wide DoS on large stores.
        """
        if not self.provider:
            logger.warning("No provider configured, cannot compress memories")
            return None

        from core.memory.compression import (
            CompressionStrategy,
            MemoryCompressor,
            CompressionResult,
        )

        safe_limit = max(1, min(int(batch_limit), 1000))
        import time as _time

        _start = _time.monotonic()
        try:
            all_memories = await self.provider.search("", limit=safe_limit)
        except Exception as e:
            logger.error(f"Failed to fetch memories for compression: {e}")
            return None

        if not all_memories:
            return CompressionResult(
                original_count=0,
                compressed_count=0,
                pruned_count=0,
                summaries_created=0,
            )

        strategy_enum = CompressionStrategy(strategy)
        compressor = MemoryCompressor(embedder=self.embedder)
        compressed_items, result = await compressor.compress(
            all_memories, strategy=strategy_enum
        )

        try:
            for item in all_memories:
                await self.provider.delete(str(item.id))

            for item in compressed_items:
                await self.provider.add(item)
        except Exception as e:
            logger.error(f"Failed to update provider during compression: {e}")
            return None

        _fetch_ms = (_time.monotonic() - _start) * 1000.0
        logger.info(
            "memory_compression_complete",
            extra={
                "original_count": result.original_count,
                "compressed_count": result.compressed_count,
                "pruned_count": result.pruned_count,
                "summaries_created": result.summaries_created,
                "fetch_ms": round(_fetch_ms, 2),
                "strategy": strategy,
                "days_threshold": days_threshold,
            },
        )

        return result

    def forget(self, memory_id: str) -> bool:
        """
        Explicitly remove a memory by its ID from working memory.

        Args:
            memory_id: The unique identifier of the memory to remove.

        Returns:
            True if removed, False if not found.
        """
        idx_to_remove = -1
        for i, item in enumerate(self._working_memory):
            if str(item.id) == memory_id:
                idx_to_remove = i
                break

        if idx_to_remove == -1:
            return False

        self._working_memory.pop(idx_to_remove)
        if idx_to_remove < len(self._working_memory_embeddings):
            self._working_memory_embeddings.pop(idx_to_remove)

        if self.provider:
            logger.warning(
                "forget() only clears working memory. "
                "Use forget_async() to also delete from the persistent provider."
            )

        return True

    async def forget_async(self, memory_id: str) -> bool:
        """
        Asynchronously remove a memory by its ID from working memory and the persistent provider.

        Args:
            memory_id: The unique identifier of the memory to remove.

        Returns:
            True if removed from working memory or provider, False otherwise.
        """
        idx_to_remove = -1
        for i, item in enumerate(self._working_memory):
            if str(item.id) == memory_id:
                idx_to_remove = i
                break

        if idx_to_remove != -1:
            self._working_memory.pop(idx_to_remove)
            if idx_to_remove < len(self._working_memory_embeddings):
                self._working_memory_embeddings.pop(idx_to_remove)

        buffer_deleted = idx_to_remove != -1
        provider_deleted = False
        if self.provider:
            provider_deleted = await self.provider.delete(memory_id)

        return buffer_deleted or provider_deleted
