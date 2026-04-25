"""
Hierarchical Memory System.

This module implements a sophisticated three-tier memory architecture
(Short-Term, Mid-Term, Long-Term) inspired by cognitive architectures
like MemoryOS. It handles the automatic promotion and consolidation
of information across different storage layers.
"""

import time
from collections import deque
from core.observability.logging import get_logger
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from .hierarchy_search import HierarchySearchMixin
from .types import MemoryItem, MemoryType

logger = get_logger(__name__)


class MemoryTier(Enum):
    """Memory storage tiers in the hierarchy."""

    STM = "short_term"  # Working memory, in-context
    MTM = "mid_term"  # Topic-segmented, summarized
    LTM = "long_term"  # Compressed, provider-backed


@dataclass
class TierConfig:
    """Configuration for a memory tier."""

    max_items: int
    auto_promote_threshold: float = 0.5  # Importance threshold for promotion
    ttl_seconds: Optional[int] = None  # Time-to-live before eviction


@dataclass
class HierarchyConfig:
    """Configuration for the hierarchical memory system."""

    stm: TierConfig = field(default_factory=lambda: TierConfig(max_items=10))
    mtm: TierConfig = field(
        default_factory=lambda: TierConfig(max_items=50, ttl_seconds=86400)
    )
    ltm: TierConfig = field(
        default_factory=lambda: TierConfig(max_items=500, ttl_seconds=604800)
    )
    auto_consolidate: bool = True  # Automatically consolidate on overflow


@dataclass
class TierStats:
    """Statistics for a memory tier."""

    tier: MemoryTier
    item_count: int
    capacity: int
    oldest_item_age_seconds: Optional[float] = None
    avg_importance: float = 0.0


class HierarchicalMemory(HierarchySearchMixin):
    """
    Advanced three-tier hierarchical memory implementation.

    Orchestrates the lifecycle of information from immediate context (STM)
    to summarized mid-term clusters (MTM) and persistent, compressed
    archival storage (LTM).

    Tiers:
    - STM: Rolling FIFO buffer for immediate conversation state.
    - MTM: Summarized topic-clusters for medium-term relevance.
    - LTM: Provider-backed persistent storage for historical facts.

    Example:
        >>> hierarchy = HierarchicalMemory()
        >>> await hierarchy.add("User prefers dark mode")
        >>> context = await hierarchy.get_context(max_tokens=2000)
    """

    def __init__(
        self,
        config: Optional[HierarchyConfig] = None,
        embedder: Optional[Any] = None,
        llm_service: Optional[Any] = None,
        provider: Optional[Any] = None,
    ):
        """
        Initialize hierarchical memory.

        Args:
            config: Configuration for tier limits and behavior
            embedder: Optional embedder for semantic operations
            llm_service: Optional LLM for summarization
            provider: Optional persistent storage provider for LTM
        """
        self.config = config or HierarchyConfig()
        self.embedder = embedder
        self._llm_service = llm_service
        self.provider = provider

        # Initialize tier storage. LTM uses a bounded deque so eviction at
        # cap (default 500) is O(1) instead of O(n).
        self._stm: List[MemoryItem] = []
        self._stm_embeddings: List[List[float]] = []
        self._mtm: List[MemoryItem] = []
        self._mtm_embeddings: List[List[float]] = []
        self._ltm: Deque[MemoryItem] = deque(maxlen=self.config.ltm.max_items)

    @property
    def llm_service(self) -> Optional[Any]:
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except ImportError:
                pass
        return self._llm_service

    async def add(
        self,
        content: str,
        tier: MemoryTier = MemoryTier.STM,
        metadata: Optional[Dict[str, Any]] = None,
        importance: float = 0.5,
    ) -> MemoryItem:
        """
        Record a new memory into a specific hierarchy tier.

        Automatically handles metadata enrichment (importance, tier source)
        and dispatches to the appropriate tier-specific storage logic.

        Args:
            content: The text payload of the memory.
            tier: The target memory tier (default: STM).
            metadata: Optional dictionary of additional context.
            importance: Numerical score (0.0 to 1.0) indicating
                       significance for future recall and promotion.

        Returns:
            MemoryItem: The structured and timestamped memory object.
        """
        metadata = metadata or {}
        metadata["importance"] = importance
        metadata["tier"] = tier.value

        item = MemoryItem(
            content=content,
            memory_type=MemoryType.SHORT_TERM
            if tier == MemoryTier.STM
            else MemoryType.LONG_TERM,
            metadata=metadata,
        )

        if tier == MemoryTier.STM:
            await self._add_to_stm(item)
        elif tier == MemoryTier.MTM:
            await self._add_to_mtm(item)
        else:
            await self._add_to_ltm(item)

        return item

    async def _resolve_embedding(
        self,
        item: MemoryItem,
        cached: Optional[List[float]] = None,
    ) -> List[float]:
        """Return ``cached`` if provided, else encode ``item.content``."""
        if cached is not None:
            return cached
        if not self.embedder:
            return []
        try:
            embedding = await self.embedder.encode(item.content)
            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()
            return embedding
        except Exception as e:
            logger.warning(f"Failed to generate embedding: {e}")
            return []

    async def _add_to_stm(
        self,
        item: MemoryItem,
        embedding: Optional[List[float]] = None,
    ) -> None:
        """Add item to short-term memory with FIFO eviction."""
        self._stm.append(item)
        self._stm_embeddings.append(await self._resolve_embedding(item, embedding))

        # Check capacity and auto-consolidate
        if len(self._stm) > self.config.stm.max_items:
            if self.config.auto_consolidate:
                await self.consolidate_stm()
            else:
                # Simple FIFO eviction
                self._stm.pop(0)
                self._stm_embeddings.pop(0)

    async def _add_to_mtm(
        self,
        item: MemoryItem,
        embedding: Optional[List[float]] = None,
    ) -> None:
        """Add item to mid-term memory with embedding cache."""
        self._mtm.append(item)
        self._mtm_embeddings.append(await self._resolve_embedding(item, embedding))

        # Check capacity and compress if needed
        if len(self._mtm) > self.config.mtm.max_items:
            if self.config.auto_consolidate:
                await self.compress_mtm()
            else:
                # Remove oldest
                self._mtm.pop(0)
                self._mtm_embeddings.pop(0)

    async def _add_to_ltm(self, item: MemoryItem) -> None:
        """Add item to long-term memory.

        ``self._ltm`` is a ``deque(maxlen=ltm.max_items)``, so capacity-based
        eviction happens automatically on append.
        """
        self._ltm.append(item)

        # Persist to provider if available
        if self.provider:
            try:
                await self.provider.add(item)
            except Exception as e:
                logger.error(f"Failed to persist to LTM provider: {e}")

    async def consolidate_stm(self, items_to_migrate: int = 5) -> int:
        """
        Migrate the oldest entries from Short-Term Memory into Mid-Term storage.

        Implements cognitive consolidation where working context is
        processed into medium-term relevance. Items are tagged with
        promotion timestamps for lifecycle tracking.

        Args:
            items_to_migrate: The number of items to shift from STM to MTM.

        Returns:
            int: The actual count of items successfully migrated.
        """
        if not self._stm:
            return 0

        # Take oldest items + their cached embeddings to skip re-encoding
        to_migrate = list(
            zip(
                self._stm[:items_to_migrate],
                self._stm_embeddings[:items_to_migrate],
            )
        )
        migrated = 0

        for item, cached_embedding in to_migrate:
            # Update tier metadata
            item.metadata["tier"] = MemoryTier.MTM.value
            item.metadata["promoted_at"] = datetime.now(timezone.utc).isoformat()
            await self._add_to_mtm(
                item, embedding=cached_embedding if cached_embedding else None
            )
            migrated += 1

        # Remove from STM
        self._stm = self._stm[items_to_migrate:]
        self._stm_embeddings = self._stm_embeddings[items_to_migrate:]

        logger.info(f"Consolidated {migrated} items from STM to MTM")
        return migrated

    async def compress_mtm(self, target_count: Optional[int] = None) -> int:
        """
        Compress MTM by summarizing clusters and promoting to LTM.

        Uses LLM summarization when available, otherwise uses simple
        concatenation.

        Args:
            target_count: Target number of items after compression

        Returns:
            Number of items compressed
        """
        if not self._mtm:
            return 0

        target = target_count or (self.config.mtm.max_items // 2)
        items_to_compress = len(self._mtm) - target

        if items_to_compress <= 0:
            return 0

        # Take oldest items for compression
        to_compress = self._mtm[:items_to_compress]

        # Create summary
        summary_content = await self._summarize_items(to_compress)
        if summary_content:
            summary_item = MemoryItem(
                content=summary_content,
                memory_type=MemoryType.LONG_TERM,
                metadata={
                    "tier": MemoryTier.LTM.value,
                    "is_summary": True,
                    "source_count": len(to_compress),
                    "compressed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            await self._add_to_ltm(summary_item)

        # Remove compressed items from MTM
        self._mtm = self._mtm[items_to_compress:]
        self._mtm_embeddings = self._mtm_embeddings[items_to_compress:]

        logger.info(f"Compressed {items_to_compress} MTM items into LTM summary")
        return items_to_compress

    async def _summarize_items(self, items: List[MemoryItem]) -> Optional[str]:
        """Create a summary of multiple memory items."""
        if not items:
            return None

        contents = [item.content for item in items]

        if self.llm_service:
            try:
                prompt = f"""Summarize the following memory fragments into a concise summary:

{chr(10).join(f"- {c}" for c in contents)}

Provide a brief, information-dense summary that preserves key facts."""

                result = await self.llm_service.generate_response(prompt)
                return result
            except Exception as e:
                logger.warning(f"LLM summarization failed: {e}")

        # Fallback: simple concatenation with truncation
        combined = " | ".join(contents)
        if len(combined) > 500:
            combined = combined[:497] + "..."
        return f"[Summary of {len(items)} items]: {combined}"

    def get_context(self, max_tokens: int = 2000) -> str:
        """
        Assemble a formatted context string for LLM injection.

        Retrieves and ranks information across all tiers, prioritizing
        immediate STM sequence, following by recent MTM clusters and
        historical LTM summaries.

        Args:
            max_tokens: Approximate character limit for the total
                        returned context.

        Returns:
            str: A formatted markdown block containing structured
                 context sections.
        """
        parts = []
        remaining = max_tokens

        # STM gets priority
        if self._stm:
            parts.append("## Recent Context")
            for item in reversed(self._stm):  # Most recent first
                line = f"- {item.content}\n"
                if len(line) > remaining:
                    break
                parts.append(line)
                remaining -= len(line)

        # MTM next
        if self._mtm and remaining > 100:
            parts.append("\n## Background")
            for item in reversed(self._mtm[-5:]):  # Last 5
                line = f"- {item.content}\n"
                if len(line) > remaining:
                    break
                parts.append(line)
                remaining -= len(line)

        # LTM summaries if space
        if self._ltm and remaining > 100:
            summaries = [i for i in self._ltm if i.metadata.get("is_summary")]
            if summaries:
                parts.append("\n## Long-term Knowledge")
                for item in summaries[-3:]:
                    line = f"- {item.content}\n"
                    if len(line) > remaining:
                        break
                    parts.append(line)
                    remaining -= len(line)

        return "".join(parts)

    _STATS_CACHE_TTL = 1.0  # seconds — coalesce metrics-scrape bursts

    def get_tier_stats(self) -> List[TierStats]:
        """Get statistics for all tiers.

        LTM holds up to ``ltm.max_items`` (default 500) entries, so the
        ``min(created_at)`` + ``mean(importance)`` pass is O(n). Endpoints
        like ``/metrics`` may scrape this multiple times per second; cache
        the computed snapshot for one second to coalesce bursts.
        """
        cached = getattr(self, "_stats_cache", None)
        if cached is not None:
            cached_at, snapshot = cached
            if time.monotonic() - cached_at < self._STATS_CACHE_TTL:
                return snapshot

        now = datetime.now(timezone.utc)
        tier_map = {
            MemoryTier.STM: "stm",
            MemoryTier.MTM: "mtm",
            MemoryTier.LTM: "ltm",
        }

        def calc_stats(tier: MemoryTier, items: Iterable[MemoryItem]) -> TierStats:
            tier_config = getattr(self.config, tier_map.get(tier, "stm"))

            count = 0
            oldest: Optional[datetime] = None
            importance_sum = 0.0
            for item in items:
                count += 1
                if oldest is None or item.created_at < oldest:
                    oldest = item.created_at
                importance_sum += item.metadata.get("importance", 0.5)

            if count == 0:
                return TierStats(
                    tier=tier,
                    item_count=0,
                    capacity=tier_config.max_items,
                )

            assert oldest is not None  # guaranteed by count > 0
            return TierStats(
                tier=tier,
                item_count=count,
                capacity=tier_config.max_items,
                oldest_item_age_seconds=(now - oldest).total_seconds(),
                avg_importance=importance_sum / count,
            )

        snapshot = [
            calc_stats(MemoryTier.STM, self._stm),
            calc_stats(MemoryTier.MTM, self._mtm),
            calc_stats(MemoryTier.LTM, self._ltm),
        ]
        self._stats_cache: Tuple[float, List[TierStats]] = (
            time.monotonic(),
            snapshot,
        )
        return snapshot

    def clear_all(self) -> Dict[str, int]:
        """Clear all tier storage. Returns counts per tier."""
        counts = {
            "stm": len(self._stm),
            "mtm": len(self._mtm),
            "ltm": len(self._ltm),
        }
        self._stm.clear()
        self._stm_embeddings.clear()
        self._mtm.clear()
        self._mtm_embeddings.clear()
        self._ltm.clear()
        return counts
