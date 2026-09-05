"""
Search Mixin for AgentMemory.

This mixin implements semantic and keyword-based retrieval strategies.
It enables the agent to 'Recall' relevant information from both the
active context window (working memory) and historical logs (long-term).
"""

import asyncio
import inspect
from typing import Any

from core.memory.types import MemoryItem, MemoryType
from core.observability.logging import get_logger
from core.utils.similarity import cosine_similarity_many

logger = get_logger(__name__)


class SearchMixin:
    """
    Extends AgentMemory with retrieval capabilities.

    Integrates vector similarity search (cosine) with standard keyword
    matching to provide a robust 'Recall' mechanism.
    """

    provider: Any | None
    embedder: Any | None
    similarity_threshold: float
    _working_memory: list[MemoryItem]
    _working_memory_embeddings: list[list[float]]

    async def _embed_query(self, query: str) -> list[float] | None:
        """Encode ``query`` once for a recall, or ``None`` when unavailable.

        Awaits an async embedder; offloads a blocking sync encode to a thread so
        the event loop is never stalled. Failures degrade to ``None`` (callers
        fall back to keyword matching) rather than aborting the recall.
        """
        embedder = self.embedder
        if embedder is None:
            return None
        try:
            if inspect.iscoroutinefunction(embedder.encode):
                vec = await embedder.encode(query)
            else:
                vec = await asyncio.to_thread(embedder.encode, query)
            if hasattr(vec, "tolist"):
                vec = vec.tolist()
            return vec
        except Exception as e:
            logger.warning(f"Query embedding failed: {e}")
            return None

    async def _semantic_search_working_memory(
        self,
        query: str,
        limit: int = 5,
        query_vector: list[float] | None = None,
    ) -> list[tuple[MemoryItem, float]]:
        if not self.embedder:
            matches = [
                (m, 1.0)
                for m in self._working_memory
                if query.lower() in m.content.lower()
            ]
            return matches[:limit]

        try:
            if query_vector is not None:
                query_embedding = query_vector
            else:
                query_embedding = await self.embedder.encode(query)
                if hasattr(query_embedding, "tolist"):
                    query_embedding = query_embedding.tolist()

            # One matmul across the working-memory buffer instead of a
            # Python-level cosine per item.
            scores = cosine_similarity_many(
                query_embedding, self._working_memory_embeddings
            )
            scored_items: list[tuple[MemoryItem, float]] = [
                (item, score)
                for item, embedding, score in zip(
                    self._working_memory, self._working_memory_embeddings, scores
                )
                if embedding and score >= self.similarity_threshold
            ]

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
        memory_types: list[MemoryType] | None = None,
        limit: int = 5,
        memory_type: MemoryType | None = None,
        include_working: bool = True,
        min_score: float | None = None,
    ) -> list[MemoryItem]:
        """
        Search for memories relevant to the given query across working and long-term memory.

        Every tier is gated by the same relevance threshold: working memory
        already filters at ``similarity_threshold``; provider (long-term) hits
        are held to it too, so a weak vector-store neighbour is not injected
        into the prompt as noise.

        Args:
            query: Natural language or keyword query.
            memory_types: List of memory categories to search in.
            limit: Maximum number of results to return.
            memory_type: Single memory category to search in (alternative to memory_types).
            include_working: Whether to include active context in the search.
            min_score: Relevance gate applied to every hit. ``None`` (default)
                uses ``similarity_threshold``; pass ``0.0`` for ungated recall.

        Returns:
            A list of relevant MemoryItem entries, sorted by similarity.
        """
        if memory_type:
            memory_types = [memory_type]
        threshold = self.similarity_threshold if min_score is None else min_score

        results: list[tuple[MemoryItem, float]] = []

        working_enabled = include_working and (
            not self.provider
            or (not memory_types or MemoryType.SHORT_TERM in memory_types)
        )

        # Embed the query once and share the vector across the working-memory
        # scan and the provider (vector-store) search, so a recall pays a single
        # encode instead of one per tier. The two searches are independent, so
        # they run concurrently — overlapping the provider's network round trip
        # with the local matmul rather than awaiting them back to back.
        query_vector = await self._embed_query(query)

        # Only hand the precomputed vector to the provider when it embeds with
        # the *same* embedder instance — otherwise its vector space differs and
        # sharing would corrupt the search, so let it re-encode. Default wiring
        # (`get_embedder()` is a cached singleton) makes this the same object.
        provider_vector = (
            query_vector
            if query_vector is not None
            and self.embedder is getattr(self.provider, "embedder", None)
            else None
        )

        async def _run_working() -> list[tuple[MemoryItem, float]]:
            return await self._semantic_search_working_memory(
                query, limit=limit, query_vector=query_vector
            )

        async def _run_provider() -> list[tuple[MemoryItem, float]]:
            assert self.provider is not None  # gated by the caller below
            type_filter = (
                memory_types[0] if memory_types and len(memory_types) == 1 else None
            )
            try:
                provider_results = await self.provider.search(
                    query,
                    memory_type=type_filter,
                    limit=limit,
                    min_score=threshold,
                    query_vector=provider_vector,
                )
                return [
                    (item, getattr(item, "score", 0.5)) for item in provider_results
                ]
            except Exception as e:
                logger.error(f"Failed to recall from provider: {e}")
                return []

        tasks = []
        if working_enabled:
            tasks.append(_run_working())
        if self.provider:
            tasks.append(_run_provider())
        for sub in await asyncio.gather(*tasks):
            results.extend(sub)

        # Post-filter as well: a provider may ignore ``min_score`` (or score on
        # a scale it does not threshold), and the gate must hold regardless.
        results = [(item, score) for item, score in results if score >= threshold]
        results.sort(key=lambda x: x[1], reverse=True)
        seen = set()
        unique_items = []
        for item, _ in results:
            if str(item.id) not in seen:
                seen.add(str(item.id))
                unique_items.append(item)

        return unique_items[:limit]
