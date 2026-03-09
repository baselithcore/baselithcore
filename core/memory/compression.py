"""
Agentic Memory Compression Module.

Provides intelligent strategies for reducing the footprint of long-term
memory while preserving semantic essence. Includes support for
relevance decay, clustering similar memories, and LLM-driven summarization.
"""

from core.observability.logging import get_logger
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from core.utils.similarity import cosine_similarity

from .types import MemoryItem, MemoryType

logger = get_logger(__name__)


class CompressionStrategy(str, Enum):
    """Memory compression strategies."""

    SUMMARIZATION = "summarization"  # LLM-based summary
    CLUSTERING = "clustering"  # Group similar memories
    PRUNING = "pruning"  # Remove low-relevance items


@dataclass
class RelevanceConfig:
    """Configuration for relevance decay calculation."""

    # Time-based decay
    half_life_days: float = 7.0  # Days until importance halves

    # Access-based boost
    access_weight: float = 0.2  # Weight of access count in score

    # Importance thresholds
    compression_threshold: float = 0.3  # Below this, compress
    pruning_threshold: float = 0.1  # Below this, consider pruning

    # Age limits
    max_age_days: int = 365  # Maximum age before forced pruning


@dataclass
class CompressionResult:
    """Result of a compression operation."""

    original_count: int
    compressed_count: int
    pruned_count: int
    summaries_created: int
    bytes_saved: int = 0

    @property
    def compression_ratio(self) -> float:
        """Calculate compression ratio."""
        if self.original_count == 0:
            return 0.0
        return 1.0 - (self.compressed_count / self.original_count)


@dataclass
class MemoryRelevance:
    """Memory item with calculated relevance score."""

    item: MemoryItem
    relevance_score: float
    age_days: float
    access_count: int = 0
    last_accessed: Optional[datetime] = None


class RelevanceCalculator:
    """
    Calculate relevance scores for memory items.

    Uses exponential decay based on age and boosts based on access frequency.
    """

    def __init__(self, config: Optional[RelevanceConfig] = None):
        """Initialize calculator with configuration."""
        self.config = config or RelevanceConfig()

    def calculate_score(
        self,
        item: MemoryItem,
        access_count: int = 0,
        last_accessed: Optional[datetime] = None,
    ) -> float:
        """
        Calculate relevance score for a memory item.

        Args:
            item: The memory item
            access_count: Number of times this memory was accessed
            last_accessed: When this memory was last accessed

        Returns:
            Relevance score between 0 and 1
        """
        now = datetime.now(timezone.utc)

        # Calculate age-based decay (exponential)
        age_days = (now - item.created_at).total_seconds() / 86400
        decay_factor = math.exp(-0.693 * age_days / self.config.half_life_days)

        # Access boost (logarithmic to prevent runaway)
        access_boost = math.log1p(access_count) * self.config.access_weight

        # Recency of access boost
        recency_boost = 0.0
        if last_accessed:
            access_age = (now - last_accessed).total_seconds() / 86400
            recency_boost = 0.1 * math.exp(-access_age / self.config.half_life_days)

        # Base importance from metadata
        base_importance = item.metadata.get("importance", 0.5)

        # Combine factors
        score = (decay_factor * base_importance) + access_boost + recency_boost

        # Clamp to [0, 1]
        return max(0.0, min(1.0, score))

    def classify_memories(
        self,
        items: List[MemoryItem],
        access_data: Optional[Dict[str, Tuple[int, Optional[datetime]]]] = None,
    ) -> Tuple[List[MemoryRelevance], List[MemoryRelevance], List[MemoryRelevance]]:
        """
        Classify memories into keep, compress, and prune buckets.

        Args:
            items: List of memory items to classify
            access_data: Dict mapping item ID to (access_count, last_accessed)

        Returns:
            Tuple of (keep_items, compress_items, prune_items)
        """
        access_data = access_data or {}
        now = datetime.now(timezone.utc)

        keep: List[MemoryRelevance] = []
        compress: List[MemoryRelevance] = []
        prune: List[MemoryRelevance] = []

        for item in items:
            item_id = str(item.id)
            access_count, last_accessed = access_data.get(item_id, (0, None))
            score = self.calculate_score(item, access_count, last_accessed)
            age_days = (now - item.created_at).total_seconds() / 86400

            relevance = MemoryRelevance(
                item=item,
                relevance_score=score,
                age_days=age_days,
                access_count=access_count,
                last_accessed=last_accessed,
            )

            # Classify based on score and age
            if age_days > self.config.max_age_days:
                prune.append(relevance)
            elif score < self.config.pruning_threshold:
                prune.append(relevance)
            elif score < self.config.compression_threshold:
                compress.append(relevance)
            else:
                keep.append(relevance)

        return keep, compress, prune


class MemoryCompressor:
    """
    Engine for semantic memory reduction.

    Implements multiple compression pipelines, allowing the system to
    summarize low-relevance items or cluster mathematically similar
    concepts to save token space and operational costs.
    """

    def __init__(
        self,
        llm_service: Optional[Any] = None,
        embedder: Optional[Any] = None,
        config: Optional[RelevanceConfig] = None,
    ):
        """
        Initialize compressor.

        Args:
            llm_service: LLM service for summarization (must have generate_response())
            embedder: Embedder for clustering (must have encode())
            config: Relevance calculation configuration
        """
        self.llm_service = llm_service
        self.embedder = embedder
        self.config = config or RelevanceConfig()
        self.relevance_calculator = RelevanceCalculator(self.config)

    async def summarize_memories(
        self,
        memories: List[MemoryItem],
        max_summary_length: int = 500,
    ) -> Optional[MemoryItem]:
        """
        Summarize multiple memories into one.

        Args:
            memories: List of memories to summarize
            max_summary_length: Maximum length of summary

        Returns:
            New MemoryItem containing summary, or None if failed
        """
        if not memories:
            return None

        if not self.llm_service:
            # Fallback: concatenate first few items
            combined = " | ".join(m.content[:100] for m in memories[:3])
            return MemoryItem(
                content=f"[Summary of {len(memories)} memories] {combined}...",
                memory_type=MemoryType.LONG_TERM,
                metadata={
                    "is_summary": True,
                    "source_count": len(memories),
                    "source_ids": [m.id for m in memories],
                },
            )

        # Build prompt for LLM summarization
        contents = "\n".join(f"- {m.content}" for m in memories)
        prompt = f"""Summarize the following {len(memories)} memory entries into a concise summary.
Keep the most important facts and insights. Maximum {max_summary_length} characters.

Memories:
{contents}

Summary:"""

        try:
            summary = await self.llm_service.generate_response(prompt)
            return MemoryItem(
                content=summary.strip()[:max_summary_length],
                memory_type=MemoryType.LONG_TERM,
                metadata={
                    "is_summary": True,
                    "source_count": len(memories),
                    "source_ids": [m.id for m in memories],
                    "compressed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as e:
            logger.error(f"Failed to summarize memories: {e}")
            return None

    async def cluster_memories(
        self,
        memories: List[MemoryItem],
        min_cluster_size: int = 3,
        similarity_threshold: float = 0.8,
    ) -> List[List[MemoryItem]]:
        """
        Cluster similar memories together.

        Args:
            memories: List of memories to cluster
            min_cluster_size: Minimum items per cluster
            similarity_threshold: Similarity threshold for clustering

        Returns:
            List of memory clusters
        """
        if not self.embedder or len(memories) < min_cluster_size:
            return [[m] for m in memories]

        try:
            import asyncio

            loop = asyncio.get_running_loop()

            # Get embeddings
            def _encode_all() -> list:
                assert self.embedder is not None  # nosec B101
                return [self.embedder.encode(m.content) for m in memories]

            embeddings = await loop.run_in_executor(None, _encode_all)

            # Simple clustering: group by similarity
            clusters: List[List[int]] = []
            assigned = set()

            for i, emb_i in enumerate(embeddings):
                if i in assigned:
                    continue

                cluster = [i]
                assigned.add(i)

                for j, emb_j in enumerate(embeddings):
                    if j in assigned or j == i:
                        continue

                    similarity = cosine_similarity(emb_i, emb_j)
                    if similarity >= similarity_threshold:
                        cluster.append(j)
                        assigned.add(j)

                if len(cluster) >= min_cluster_size:
                    clusters.append(cluster)

            # Convert indices to memory items
            return [[memories[i] for i in cluster] for cluster in clusters]

        except Exception as e:
            logger.error(f"Clustering failed: {e}")
            return [[m] for m in memories]

    async def compress(
        self,
        memories: List[MemoryItem],
        strategy: CompressionStrategy = CompressionStrategy.SUMMARIZATION,
        access_data: Optional[Dict[str, Tuple[int, Optional[datetime]]]] = None,
    ) -> Tuple[List[MemoryItem], CompressionResult]:
        """
        Compress memories using the specified strategy.

        Args:
            memories: List of memories to process
            strategy: Compression strategy to use
            access_data: Optional access tracking data

        Returns:
            Tuple of (compressed_memories, compression_result)
        """
        original_count = len(memories)

        # Classify by relevance
        keep, to_compress, to_prune = self.relevance_calculator.classify_memories(
            memories, access_data
        )

        result_memories: List[MemoryItem] = [r.item for r in keep]
        summaries_created = 0

        if strategy == CompressionStrategy.SUMMARIZATION:
            # Summarize low-relevance items
            if to_compress:
                summary = await self.summarize_memories([r.item for r in to_compress])
                if summary:
                    result_memories.append(summary)
                    summaries_created = 1

        elif strategy == CompressionStrategy.CLUSTERING:
            # Cluster and summarize similar items
            compress_items = [r.item for r in to_compress]
            clusters = await self.cluster_memories(compress_items)

            for cluster in clusters:
                if len(cluster) > 1:
                    summary = await self.summarize_memories(cluster)
                    if summary:
                        result_memories.append(summary)
                        summaries_created += 1
                else:
                    result_memories.extend(cluster)

        elif strategy == CompressionStrategy.PRUNING:
            # Just keep high-relevance items
            pass  # Already handled by keeping only 'keep' items

        result = CompressionResult(
            original_count=original_count,
            compressed_count=len(result_memories),
            pruned_count=len(to_prune),
            summaries_created=summaries_created,
        )

        logger.info(
            f"Compression complete: {original_count} -> {len(result_memories)} "
            f"(pruned {len(to_prune)}, created {summaries_created} summaries)"
        )

        return result_memories, result


# Convenience function
async def compress_memories(
    memories: List[MemoryItem],
    llm_service: Optional[Any] = None,
    strategy: CompressionStrategy = CompressionStrategy.SUMMARIZATION,
) -> Tuple[List[MemoryItem], CompressionResult]:
    """
    Convenience function to compress memories.

    Args:
        memories: Memories to compress
        llm_service: Optional LLM service for summarization
        strategy: Compression strategy

    Returns:
        Tuple of (compressed_memories, result)
    """
    compressor = MemoryCompressor(llm_service=llm_service)
    return await compressor.compress(memories, strategy=strategy)
