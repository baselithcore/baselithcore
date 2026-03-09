"""
Hierarchical Memory Search Module.

Provides cross-tier search capabilities for the HierarchicalMemory system.
Coordinates between STM FIFO search, MTM cluster search, and LTM provider
vector search.
"""

from core.observability.logging import get_logger
from typing import Any, List, Optional, Tuple

from core.utils.similarity import cosine_similarity

from .types import MemoryItem

logger = get_logger(__name__)


class HierarchySearchMixin:
    """
    Recall engine for hierarchical memory stores.

    Facilitates 'Recall' operations by intelligently querying active
    buffers, mid-term summaries, and persistent backends based on the
    requested tier configuration and available embedders.
    """

    # Attributes declared for type checkers (set by the host class)
    _stm: List[MemoryItem]
    _stm_embeddings: List[List[float]]
    _mtm: List[MemoryItem]
    _mtm_embeddings: List[List[float]]
    _ltm: List[MemoryItem]
    embedder: Optional[Any]
    provider: Optional[Any]

    async def recall(
        self,
        query: str,
        tiers: Optional[List[Any]] = None,
        limit: int = 5,
    ) -> List[MemoryItem]:
        """
        Recall memories relevant to a query across hierarchies of storage.

        Coordinates the parallel or sequential search through short-term,
        mid-term, and long-term memory tiers. Results are aggregated,
        scored, and ranked by semantic or keyword relevance.

        Args:
            query: The search string to match against memory contents.
            tiers: Optional list of MemoryTier enums to restrict search scope.
                   Defaults to searching all tiers (STM, MTM, LTM).
            limit: Maximum number of relevant memories to return.

        Returns:
            List[MemoryItem]: A ranked list of memories matching the query,
                             limited to the specified count.
        """
        from .hierarchy import MemoryTier

        tiers = tiers or [MemoryTier.STM, MemoryTier.MTM, MemoryTier.LTM]
        results: List[Tuple[MemoryItem, float]] = []

        if MemoryTier.STM in tiers:
            results.extend(await self._search_stm(query, limit))

        if MemoryTier.MTM in tiers:
            results.extend(
                await self._search_in_memory(
                    self._mtm, self._mtm_embeddings, query, limit
                )
            )

        if MemoryTier.LTM in tiers:
            results.extend(await self._search_ltm(query, limit))

        # Sort by score and return top results
        results.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in results[:limit]]

    async def _search_stm(
        self, query: str, limit: int
    ) -> List[Tuple[MemoryItem, float]]:
        """
        Perform a focused search within the Short-Term Memory (STM) buffer.

        Uses semantic embeddings if an embedder is available; otherwise,
        falls back to case-insensitive keyword matching.

        Args:
            query: The search string.
            limit: Maximum results from this tier.

        Returns:
            List[Tuple[MemoryItem, float]]: Pairs of (item, score) from STM.
        """
        if not self._stm:
            return []

        if self.embedder and self._stm_embeddings:
            try:
                query_embedding = await self.embedder.encode(query)
                if hasattr(query_embedding, "tolist"):
                    query_embedding = query_embedding.tolist()

                scored = []
                for item, emb in zip(self._stm, self._stm_embeddings):
                    if emb:
                        score = cosine_similarity(query_embedding, emb)
                        if score > 0.5:  # Threshold
                            scored.append((item, score))

                scored.sort(key=lambda x: x[1], reverse=True)
                return scored[:limit]
            except Exception as e:
                logger.warning(f"Semantic STM search failed: {e}")

        # Fallback to keyword search
        query_lower = query.lower()
        return [
            (item, 1.0) for item in self._stm if query_lower in item.content.lower()
        ][:limit]

    async def _search_in_memory(
        self,
        items: List[MemoryItem],
        embeddings: List[List[float]],
        query: str,
        limit: int,
    ) -> List[Tuple[MemoryItem, float]]:
        """
        Generalized semantic search for in-memory collections of items.

        Args:
            items: List of MemoryItem objects to search.
            embeddings: Parallel list of vector embeddings for the items.
            query: The search string.
            limit: Maximum results.

        Returns:
            List[Tuple[MemoryItem, float]]: Ranked (item, score) pairs.
        """
        if not items:
            return []

        if self.embedder and embeddings:
            try:
                query_embedding = await self.embedder.encode(query)
                if hasattr(query_embedding, "tolist"):
                    query_embedding = query_embedding.tolist()

                scored = []
                for item, emb in zip(items, embeddings):
                    if emb:
                        score = cosine_similarity(query_embedding, emb)
                        if score > 0.5:
                            scored.append((item, score))

                scored.sort(key=lambda x: x[1], reverse=True)
                return scored[:limit]
            except Exception as e:
                logger.warning(f"Semantic tier search failed: {e}")

        # Fallback to keyword search
        query_lower = query.lower()
        return [(item, 1.0) for item in items if query_lower in item.content.lower()][
            :limit
        ]

    async def _search_ltm(
        self, query: str, limit: int
    ) -> List[Tuple[MemoryItem, float]]:
        """
        Query the persistent Long-Term Memory (LTM) backend.

        Leverages the configured vector provider (e.g., Qdrant) for
        efficient large-scale retrieval. Falls back to keyword search
        on the local LTM cache if the provider is unavailable.

        Args:
            query: The search string.
            limit: Maximum results from persistent storage.

        Returns:
            List[Tuple[MemoryItem, float]]: Matches from LTM with scores.
        """
        # Use provider for vector search if available
        if self.provider:
            try:
                results = await self.provider.search(query, limit=limit)
                return [(item, getattr(item, "score", 0.5)) for item in results]
            except Exception as e:
                logger.warning(f"LTM provider search failed: {e}")

        # Fallback to in-memory keyword search on LTM cache
        if not self._ltm:
            return []
        query_lower = query.lower()
        return [
            (item, 1.0) for item in self._ltm if query_lower in item.content.lower()
        ][:limit]
