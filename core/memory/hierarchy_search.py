"""
Hierarchical Memory Search Module.

Provides cross-tier search capabilities for the HierarchicalMemory system.
Coordinates between STM FIFO search, MTM cluster search, and LTM provider
vector search.
"""

import asyncio
import os
from collections import Counter
from collections.abc import Iterable
from typing import Any

from core.observability.logging import get_logger
from core.utils.concurrency import run_inference
from core.utils.similarity import cosine_similarity_many

from .embedding_compat import encode_flexible
from .hybrid_search import BM25Index, HybridSearcher, ScoredHit, bm25_doc_stats
from .types import MemoryItem

logger = get_logger(__name__)

# Fuse dense (cosine) recall with a BM25 keyword pass via Reciprocal Rank
# Fusion. Off-switch preserves the pure-cosine behaviour exactly.
_HYBRID_RECALL_ENABLED = os.getenv("BASELITH_MEMORY_HYBRID_RECALL", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Above this corpus size the BM25 fusion pass (tokenization + scoring + RRF —
# pure CPU) is offloaded to the inference executor instead of running inline
# on the event loop; below it the thread hand-off costs more than the work.
_FUSE_OFFLOAD_THRESHOLD = 64


def _normalize_content(text: str) -> str:
    """Whitespace-normalized, lowercased key for near-duplicate dedup."""
    return " ".join(text.lower().split())


class HierarchySearchMixin:
    """
    Recall engine for hierarchical memory stores.

    Facilitates 'Recall' operations by intelligently querying active
    buffers, mid-term summaries, and persistent backends based on the
    requested tier configuration and available embedders.
    """

    # Attributes declared for type checkers (set by the host class)
    _stm: list[MemoryItem]
    _stm_embeddings: list[list[float]]
    _mtm: list[MemoryItem]
    _mtm_embeddings: list[list[float]]
    _ltm: Iterable[MemoryItem]  # deque(maxlen=...) in HierarchicalMemory
    embedder: Any | None
    provider: Any | None

    # BM25 memoization (set lazily by _build_bm25_index; content-keyed, so a
    # mutated or evicted item can never serve stale stats).
    _bm25_stats_cache: dict[str, tuple[str, Counter[str], int]]
    _bm25_index_cache: tuple[dict[str, str], BM25Index] | None

    async def recall(
        self,
        query: str,
        tiers: list[Any] | None = None,
        limit: int = 5,
    ) -> list[MemoryItem]:
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
        results: list[tuple[MemoryItem, float]] = []

        # Encode the query once and share it across STM/MTM searches —
        # embedder calls are the dominant cost of a recall, and each tier
        # used to re-encode the same query independently.
        query_embedding: list[float] | None = None
        if self.embedder and (
            (MemoryTier.STM in tiers and self._stm_embeddings)
            or (MemoryTier.MTM in tiers and self._mtm_embeddings)
        ):
            try:
                query_embedding = await encode_flexible(self.embedder, query)
            except Exception as e:
                logger.warning(f"Query embedding failed, using keyword search: {e}")

        # The three tiers are independent reads — STM/MTM are local matmuls, LTM
        # is a provider (network) call — so run them concurrently instead of
        # awaiting each in turn. LTM reuses the query embedding when the provider
        # shares this store's embedder instance (see _search_ltm).
        tier_tasks = []
        if MemoryTier.STM in tiers:
            tier_tasks.append(
                self._search_stm(query, limit, query_embedding=query_embedding)
            )
        if MemoryTier.MTM in tiers:
            tier_tasks.append(
                self._search_in_memory(
                    self._mtm,
                    self._mtm_embeddings,
                    query,
                    limit,
                    query_embedding=query_embedding,
                )
            )
        if MemoryTier.LTM in tiers:
            tier_tasks.append(
                self._search_ltm(query, limit, query_embedding=query_embedding)
            )
        for tier_results in await asyncio.gather(*tier_tasks):
            results.extend(tier_results)

        if _HYBRID_RECALL_ENABLED:
            # Fusion is pure CPU; past the threshold it runs on the inference
            # executor so a large corpus never stalls the event loop (the
            # reranker below already does the same).
            corpus_size = len(results) + len(self._stm) + len(self._mtm)
            if corpus_size > _FUSE_OFFLOAD_THRESHOLD:
                fused = await run_inference(
                    self._fuse_recall, query, results, tiers, limit
                )
            else:
                fused = self._fuse_recall(query, results, tiers, limit)
            return await self._maybe_rerank(query, fused)

        # Pure-cosine path (hybrid disabled): sort by score, take top-k.
        results.sort(key=lambda x: x[1], reverse=True)
        return await self._maybe_rerank(query, [item for item, _ in results[:limit]])

    async def _maybe_rerank(
        self, query: str, items: list[MemoryItem]
    ) -> list[MemoryItem]:
        """Opt-in cross-encoder re-ordering of the recalled top-k.

        Gated by ``BASELITH_MEMORY_RERANK`` (default off: the cross-encoder
        is a heavy optional dependency and adds hot-path latency). Reuses the
        chat pipeline's reranker; fail-open — any error returns the fused
        order unchanged. The k results themselves never change, only their
        order, so downstream truncation semantics are preserved.
        """
        if len(items) < 2 or os.getenv(
            "BASELITH_MEMORY_RERANK", "false"
        ).lower() not in ("1", "true", "yes", "on"):
            return items
        try:
            # Lazy import kept: core.chat.dependencies pulls optional heavy deps.
            from core.chat.dependencies import get_reranker

            # Any: same loose typing as the chat pipeline's RerankerProtocol —
            # the concrete CrossEncoder's overloads don't accept list[tuple]
            # under strict checking even though it's the documented input.
            reranker: Any = get_reranker()
            if reranker is None:
                return items
            pairs = [(query, item.content) for item in items]
            raw = await run_inference(reranker.predict, pairs)
            scores = raw.tolist() if hasattr(raw, "tolist") else list(raw)
            ranked = sorted(zip(items, scores), key=lambda x: x[1], reverse=True)
            return [item for item, _ in ranked]
        except Exception as e:
            logger.warning(f"Memory recall rerank failed (keeping fused order): {e}")
            return items

    def _inmemory_corpus(self, tiers: list[Any]) -> list[MemoryItem]:
        """STM+MTM items eligible for the BM25 keyword pass, per requested tiers."""
        from .hierarchy import MemoryTier

        corpus: list[MemoryItem] = []
        if MemoryTier.STM in tiers:
            corpus.extend(self._stm)
        if MemoryTier.MTM in tiers:
            corpus.extend(self._mtm)
        return corpus

    def _fuse_recall(
        self,
        query: str,
        dense_results: list[tuple[MemoryItem, float]],
        tiers: list[Any],
        limit: int,
    ) -> list[MemoryItem]:
        """Fuse dense (cosine) hits with a BM25 keyword pass via RRF, then dedup.

        The dense stream preserves the existing relevance filtering (cosine
        threshold / keyword fallback per tier). BM25 rescues exact keyword hits
        the dense threshold dropped; Reciprocal Rank Fusion merges the two
        rank-wise (scale-free, so STM/MTM/LTM scores no longer have to be on the
        same scale). Near-duplicate contents across tiers are collapsed.
        """
        items_by_id: dict[str, MemoryItem] = {}
        dense_hits: list[ScoredHit] = []
        for item, score in sorted(dense_results, key=lambda x: x[1], reverse=True):
            doc_id = str(id(item))
            items_by_id.setdefault(doc_id, item)
            dense_hits.append(ScoredHit(doc_id=doc_id, score=score))

        # BM25 corpus split in two: the in-memory tiers form the *base* index
        # (stable between writes, so its cache actually hits), while dense
        # candidates not already in it — LTM/provider hits, different every
        # query — ride on top via search_with_extra. Scoring is arithmetic-
        # identical to one unified index, but the per-recall cost drops from
        # an O(corpus) rebuild to O(query_terms × (matches + extras)).
        base_docs: dict[str, str] = {}
        for item in self._inmemory_corpus(tiers):
            doc_id = str(id(item))
            items_by_id.setdefault(doc_id, item)
            base_docs[doc_id] = item.content
        extra_docs = {
            doc_id: bm25_doc_stats(item.content)
            for doc_id, item in items_by_id.items()
            if doc_id not in base_docs
        }

        bm25_hits: list[ScoredHit] = []
        if base_docs or extra_docs:
            index = self._build_bm25_index(base_docs)
            bm25_hits = index.search_with_extra(
                query, extra_docs, top_k=max(limit * 4, 10)
            )

        if not dense_hits and not bm25_hits:
            return []

        fused = HybridSearcher().fuse(
            bm25=bm25_hits, dense=dense_hits, top_k=max(len(items_by_id), 1)
        )

        seen: set[str] = set()
        out: list[MemoryItem] = []
        for hit in fused:
            candidate = items_by_id.get(hit.doc_id)
            if candidate is None:
                continue
            key = _normalize_content(candidate.content)
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
            if len(out) >= limit:
                break
        return out

    def _build_bm25_index(self, docs: dict[str, str]) -> BM25Index:
        """BM25 index over ``docs`` with tokenization memoized across recalls.

        Two cache levels, both invalidated by content equality so scoring is
        bit-identical to a fresh build:

        1. Whole-index reuse when the ``doc_id -> content`` mapping is
           unchanged since the previous recall (typical for STM/MTM-only
           recalls between writes).
        2. Per-document token stats otherwise — only new/changed documents
           are re-tokenized (LTM candidates vary per query; the in-memory
           corpus rarely does).
        """
        cached_index = getattr(self, "_bm25_index_cache", None)
        if cached_index is not None and cached_index[0] == docs:
            return cached_index[1]

        stats_cache = getattr(self, "_bm25_stats_cache", {})
        fresh_stats: dict[str, tuple[str, Counter[str], int]] = {}
        tokenized: dict[str, tuple[Counter[str], int]] = {}
        for doc_id, content in docs.items():
            hit = stats_cache.get(doc_id)
            if hit is not None and hit[0] == content:
                stats = (hit[1], hit[2])
            else:
                stats = bm25_doc_stats(content)
            fresh_stats[doc_id] = (content, stats[0], stats[1])
            tokenized[doc_id] = stats
        # Replace (not update) so entries for evicted items are dropped.
        self._bm25_stats_cache = fresh_stats

        index = BM25Index()
        index.index_tokenized(tokenized)
        self._bm25_index_cache = (dict(docs), index)
        return index

    async def _search_stm(
        self,
        query: str,
        limit: int,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryItem, float]]:
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
                if query_embedding is None:
                    query_embedding = await encode_flexible(self.embedder, query)
                assert query_embedding is not None

                # One matmul over the whole tier instead of a Python-level
                # cosine per stored item.
                scores = cosine_similarity_many(query_embedding, self._stm_embeddings)
                scored = [
                    (item, score)
                    for item, emb, score in zip(self._stm, self._stm_embeddings, scores)
                    if emb and score > 0.5  # Threshold
                ]

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
        items: list[MemoryItem],
        embeddings: list[list[float]],
        query: str,
        limit: int,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryItem, float]]:
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
                if query_embedding is None:
                    query_embedding = await encode_flexible(self.embedder, query)
                assert query_embedding is not None

                scores = cosine_similarity_many(query_embedding, embeddings)
                scored = [
                    (item, score)
                    for item, emb, score in zip(items, embeddings, scores)
                    if emb and score > 0.5
                ]

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
        self,
        query: str,
        limit: int,
        query_embedding: list[float] | None = None,
    ) -> list[tuple[MemoryItem, float]]:
        """
        Query the persistent Long-Term Memory (LTM) backend.

        Leverages the configured vector provider (e.g., Qdrant) for
        efficient large-scale retrieval. Falls back to keyword search
        on the local LTM cache if the provider is unavailable.

        Args:
            query: The search string.
            limit: Maximum results from persistent storage.
            query_embedding: Precomputed query vector reused to skip the
                provider's own encode — only when the provider embeds with this
                store's embedder instance (identity-checked to avoid mixing
                vector spaces); otherwise the provider re-encodes from ``query``.

        Returns:
            List[Tuple[MemoryItem, float]]: Matches from LTM with scores.
        """
        # Use provider for vector search if available
        if self.provider:
            try:
                shared_vector = (
                    query_embedding
                    if query_embedding is not None
                    and self.embedder is getattr(self.provider, "embedder", None)
                    else None
                )
                results = await self.provider.search(
                    query, limit=limit, query_vector=shared_vector
                )
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
