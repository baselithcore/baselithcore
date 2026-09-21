"""
Optimization Mixin for AgentMemory.

This mixin manages the lifecycle of memories beyond simple storage.
It provides high-level operations for consolidating working memory into
long-term storage, compressing old memories via summarization, and
graceful 'forgetting' (deletion).
"""

from typing import TYPE_CHECKING, Any, Optional

from core.memory.optimization_batch import add_items, delete_items
from core.memory.types import MemoryItem, MemoryType
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.memory.compression import CompressionResult

logger = get_logger(__name__)

# Max concurrent vector-store round-trips when rewriting memories in bulk, so a
# large compaction (up to ``safe_limit`` items) can't open thousands at once.
_PROVIDER_FANOUT_LIMIT = 8


class MemoryCompactionUnsupported(RuntimeError):
    """The configured provider cannot support a safe compaction run."""


def _pruned_ids(
    read: list[MemoryItem], compressed_items: list[MemoryItem], folded: set[str]
) -> set[str]:
    """Derive which of the items read were dropped as below-threshold.

    The compressor reports a ``pruned_count`` but not which items it scored
    that way. Rather than re-run its thresholds here — two copies of a decay
    curve drift apart — the set is derived: anything read that neither
    survived into the output nor went into a summary was pruned.

    Args:
        read: The batch that was enumerated.
        compressed_items: What the compressor returned.
        folded: Ids already accounted for by a summary.

    Returns:
        The pruned ids, as strings.
    """
    survivors = {
        str(item.id) for item in compressed_items if not item.metadata.get("is_summary")
    }
    return {str(item.id) for item in read} - survivors - folded


def _folded_source_ids(compressed_items: list[MemoryItem]) -> set[str]:
    """Collect the ids the compressor reports as folded into a summary.

    Only these are deleted. A summary that does not record its sources deletes
    nothing, which costs a little space and cannot lose a memory.

    Args:
        compressed_items: What the compressor returned.

    Returns:
        The source ids, as strings.
    """
    consumed: set[str] = set()
    for item in compressed_items:
        if not item.metadata.get("is_summary"):
            continue
        for source_id in item.metadata.get("source_ids") or ():
            consumed.add(str(source_id))
    return consumed


class OptimizationMixin:
    """
    Extends AgentMemory with maintenance operations.

    Ensures the memory system remains performant by preventing context
    bloat and enabling automated archiving/consolidation.
    """

    provider: Any | None
    embedder: Any | None
    _working_memory: list[MemoryItem]
    _working_memory_embeddings: list[list[float]]

    async def consolidate(self) -> None:
        """Merge fragmented memories into more cohesive summaries."""
        if not self.provider:
            return

        for item in self._working_memory:
            item.memory_type = MemoryType.EPISODIC
        # One embedding pass and one upsert for the whole batch where the
        # provider supports it, instead of a round trip per item.
        await add_items(
            self.provider, self._working_memory, fanout_limit=_PROVIDER_FANOUT_LIMIT
        )

        logger.info("Memory consolidation complete")

    async def _enumerate_for_compression(self, safe_limit: int) -> list[MemoryItem]:
        """Read the batch compaction will act on, in provider order.

        Raises:
            MemoryCompactionUnsupported: The provider cannot enumerate. Not a
                soft failure: compaction deletes what it reads, so an
                approximate read is a silent data-loss path.
        """
        list_items = getattr(self.provider, "list_items", None)
        if not callable(list_items):
            raise MemoryCompactionUnsupported(
                f"{type(self.provider).__name__} cannot enumerate its contents; "
                "compaction needs a deterministic read of what it is about to "
                "delete."
            )
        page, _next_offset = await list_items(limit=safe_limit)
        return list(page)

    async def compress_old_memories(
        self,
        days_threshold: int = 7,
        strategy: str = "summarization",
        batch_limit: int = 500,
        prune: bool = False,
    ) -> Optional["CompressionResult"]:
        """Fold low-relevance memories into summaries to reclaim space.

        Destructive: the source items that go into a summary are deleted once
        it is written. Three preconditions therefore gate the run, and each
        one aborts instead of degrading.

        * **A provider that can enumerate.** The batch used to come from
          ``provider.search("")`` — the nearest neighbours of the empty
          string's embedding, an arbitrary slice that changed between runs —
          and every id in it was then deleted. Compaction now reads the batch
          in provider order, so what it deletes is what it looked at.
        * **A real summarizer.** Without an ``llm_service`` the compressor
          falls back to ``" | ".join(m.content[:100] for m in memories[:3])``.
          Trading a batch of memories for a 300-character truncation of three
          of them is data loss, not compression, so it is refused.
        * **Sources it can address.** Only the items actually folded into a
          summary are deleted, and only when the summary records which ones
          those were. Items the compressor chose to keep are left alone rather
          than deleted and rewritten.

        Pruning is separate and opt-in. The relevance calculator also drops
        items below ``pruning_threshold`` from its output; deleting those is a
        discard, not a compression, since nothing is written in their place.
        The old delete-everything-we-read step performed that discard as a
        side effect of compaction, so a caller asking to reclaim space silently
        lost every memory the decay curve had aged out. The candidates are
        reported as ``pruned_count`` whether or not ``prune`` is set, so an
        operator can see the count before authorising the deletion.

        Args:
            days_threshold: Age threshold for compression (applied by the
                relevance calculator, not at this layer).
            strategy: Compression strategy name.
            batch_limit: Max memories to read per run. Keeps the operation
                bounded on large stores.
            prune: Also delete the items the calculator scored below the
                pruning threshold. Off by default.

        Returns:
            The compression result, or ``None`` when a precondition failed.
        """
        if not self.provider:
            logger.warning("No provider configured, cannot compress memories")
            return None

        from core.memory.compression import (
            CompressionResult,
            CompressionStrategy,
            MemoryCompressor,
        )

        llm_service = getattr(self, "llm_service", None)
        if llm_service is None:
            logger.warning(
                "memory_compression_skipped_no_summarizer",
                extra={"strategy": strategy},
            )
            return None

        safe_limit = max(1, min(int(batch_limit), 1000))
        import time as _time

        _start = _time.monotonic()
        try:
            all_memories = await self._enumerate_for_compression(safe_limit)
        except MemoryCompactionUnsupported as e:
            logger.warning("memory_compression_unsupported", extra={"reason": str(e)})
            return None
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
        compressor = MemoryCompressor(llm_service=llm_service, embedder=self.embedder)
        compressed_items, result = await compressor.compress(
            all_memories, strategy=strategy_enum
        )

        consumed = _folded_source_ids(compressed_items)
        removable = set(consumed)
        if prune:
            removable |= _pruned_ids(all_memories, compressed_items, consumed)

        new_summaries = [
            item for item in compressed_items if item.metadata.get("is_summary")
        ]
        if not removable and not new_summaries:
            logger.info(
                "memory_compression_noop",
                extra={
                    "original_count": result.original_count,
                    "prune_candidates": result.pruned_count,
                },
            )
            return result

        try:
            # Write the summaries before deleting their sources: a crash
            # between the two then leaves a recoverable duplicate rather than
            # a hole. Providers with a batch API do each phase in one
            # round-trip; the fallback fans out under a concurrency ceiling.
            await add_items(
                self.provider, new_summaries, fanout_limit=_PROVIDER_FANOUT_LIMIT
            )
            await delete_items(
                self.provider,
                sorted(removable),
                fanout_limit=_PROVIDER_FANOUT_LIMIT,
            )
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
                "deleted_count": len(removable),
                "pruned": prune,
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
