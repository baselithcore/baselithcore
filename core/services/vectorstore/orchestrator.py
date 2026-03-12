"""
VectorStore Retrieval and Ranking Orchestrator.

Handles the two-stage retrieval process:
1. Vector similarity search across providers.
2. Semantic re-ranking using cross-encoders.
"""

import hashlib
import json
from typing import Sequence

from core.models.domain import Document, SearchResult
from core.observability.logging import get_logger
from core.context import get_current_tenant_id

logger = get_logger(__name__)


class SearchOrchestrator:
    """
    Orchestrates search retrieval and re-ranking phases.
    """

    def __init__(self, config, provider, search_cache=None):
        self.config = config
        self.provider = provider
        self.search_cache = search_cache
        self._search_cache_enabled = getattr(self.config, "search_cache_enabled", True)
        self._search_cache_ttl = getattr(self.config, "search_cache_ttl", 300)

    async def search(
        self,
        query_vector: Sequence[float],
        k: int | None = None,
        collection_name: str | None = None,
        use_cache: bool = True,
        query_text: str | None = None,
        rerank: bool = False,
        **kwargs,
    ) -> Sequence[SearchResult]:
        """
        Perform a vector similarity search with tenant isolation and optional re-ranking.
        """
        collection_name = collection_name or self.config.collection_name
        k = k or self.config.search_limit
        tenant_id = get_current_tenant_id()

        # Enforce multi-tenancy filter.
        kwargs["tenant_id"] = tenant_id

        # Determine retrieval depth: fetch more if we plan to re-rank.
        retrieval_limit = k
        if rerank and query_text:
            retrieval_limit = max(k * 3, 20)

        cache_key = None
        if use_cache and self.search_cache and self._search_cache_enabled:
            vector_hash = hashlib.sha256(
                json.dumps(list(query_vector)[:10]).encode()
            ).hexdigest()[:16]
            cache_key = f"{collection_name}:{tenant_id}:{retrieval_limit}:{vector_hash}:rr={rerank}"

            cached_results = await self.search_cache.get(cache_key)
            if cached_results is not None:
                logger.debug(f"Search cache hit for key {cache_key[:20]}...")
                return [
                    SearchResult(**res) if isinstance(res, dict) else res
                    for res in cached_results
                ]

        try:
            # 1. First stage: Vector retrieval.
            results = await self.provider.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=retrieval_limit,
                **kwargs,
            )

            # Map provider results to internal SearchResult domain model.
            search_results = []
            for hit in results:
                payload = getattr(hit, "payload", {}) or {}
                doc = Document(
                    id=payload.get("document_id", str(getattr(hit, "id", ""))),
                    content=payload.get("text", payload.get("chunk_body", "")),
                    metadata=payload,
                    vector=getattr(hit, "vector", None),
                )
                search_results.append(
                    SearchResult(document=doc, score=getattr(hit, "score", 0.0))
                )

            # 2. Second stage: Cross-encoder re-ranking.
            if rerank and query_text and search_results:
                try:
                    from core.services.retrieval.reranker import get_reranker

                    reranker = get_reranker()
                    search_results = reranker.rerank(
                        query=query_text, results=search_results, top_k=k
                    )
                except Exception as e:
                    logger.warning(
                        f"Re-ranking failed, falling back to original vector scores: {e}"
                    )
                    search_results = search_results[:k]

            # Update cache with final results.
            if (
                use_cache
                and self.search_cache
                and self._search_cache_enabled
                and search_results
                and cache_key
            ):
                try:
                    serializable = [sr.model_dump() for sr in search_results]
                    await self.search_cache.set(
                        cache_key, serializable, ttl=self._search_cache_ttl
                    )
                except Exception as cache_err:
                    logger.debug(f"Search result caching failed: {cache_err}")

            return search_results

        except Exception as e:
            logger.error(f"Search operation failed: {e}")
            from core.services.vectorstore.exceptions import VectorStoreError

            raise VectorStoreError(f"Search failed: {e}") from e
