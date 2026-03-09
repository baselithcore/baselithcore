"""
Search Mixin for AgentMemory.

This mixin implements semantic and keyword-based retrieval strategies.
It enables the agent to 'Recall' relevant information from both the
active context window (working memory) and historical logs (long-term).
"""

from core.observability.logging import get_logger
from typing import Any, List, Optional, Tuple

from core.utils.similarity import cosine_similarity
from core.memory.types import MemoryItem, MemoryType

logger = get_logger(__name__)


class SearchMixin:
    """
    Extends AgentMemory with retrieval capabilities.

    Integrates vector similarity search (cosine) with standard keyword
    matching to provide a robust 'Recall' mechanism.
    """

    provider: Optional[Any]
    embedder: Optional[Any]
    similarity_threshold: float
    _working_memory: List[MemoryItem]
    _working_memory_embeddings: List[List[float]]

    async def _semantic_search_working_memory(
        self, query: str, limit: int = 5
    ) -> List[Tuple[MemoryItem, float]]:
        if not self.embedder:
            matches = [
                (m, 1.0)
                for m in self._working_memory
                if query.lower() in m.content.lower()
            ]
            return matches[:limit]

        try:
            query_embedding = await self.embedder.encode(query)
            if hasattr(query_embedding, "tolist"):
                query_embedding = query_embedding.tolist()

            scored_items: List[Tuple[MemoryItem, float]] = []
            for item, embedding in zip(
                self._working_memory, self._working_memory_embeddings
            ):
                if embedding:
                    score = cosine_similarity(query_embedding, embedding)
                    if score >= self.similarity_threshold:
                        scored_items.append((item, score))

            scored_items.sort(key=lambda x: x[1], reverse=True)
            return scored_items[:limit]

        except Exception as e:
            logger.warning(
                f"Semantic working memory search failed: {e}, falling back to keyword"
            )
            matches = [
                (m, 1.0)
                for m in self._working_memory
                if query.lower() in m.content.lower()
            ]
            return matches[:limit]

    async def recall(
        self,
        query: str,
        memory_types: Optional[List[MemoryType]] = None,
        limit: int = 5,
        memory_type: Optional[MemoryType] = None,
        include_working: bool = True,
    ) -> List[MemoryItem]:
        """
        Search for memories relevant to the given query across working and long-term memory.

        Args:
            query: Natural language or keyword query.
            memory_types: List of memory categories to search in.
            limit: Maximum number of results to return.
            memory_type: Single memory category to search in (alternative to memory_types).
            include_working: Whether to include active context in the search.

        Returns:
            A list of relevant MemoryItem entries, sorted by similarity.
        """
        if memory_type:
            memory_types = [memory_type]

        results: List[Tuple[MemoryItem, float]] = []

        if include_working and (
            not self.provider
            or (not memory_types or MemoryType.SHORT_TERM in memory_types)
        ):
            buffer_results = await self._semantic_search_working_memory(
                query, limit=limit
            )
            results.extend(buffer_results)

        if self.provider:
            try:
                type_filter = (
                    memory_types[0] if memory_types and len(memory_types) == 1 else None
                )
                provider_results = await self.provider.search(
                    query, memory_type=type_filter, limit=limit
                )
                for item in provider_results:
                    score = getattr(item, "score", 0.5)
                    results.append((item, score))
            except Exception as e:
                logger.error(f"Failed to recall from provider: {e}")

        results.sort(key=lambda x: x[1], reverse=True)
        seen = set()
        unique_items = []
        for item, _ in results:
            if str(item.id) not in seen:
                seen.add(str(item.id))
                unique_items.append(item)

        return unique_items[:limit]
