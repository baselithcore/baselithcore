"""
VectorStore Retrieval and Ranking Orchestrator.

Handles the two-stage retrieval process:
1. Vector similarity search across providers.
2. Semantic re-ranking using cross-encoders.
"""

import hashlib
import json
import struct
from collections.abc import Sequence
from typing import Any

from core.context import get_current_tenant_id
from core.models.domain import Document, SearchResult
from core.observability.logging import get_logger

logger = get_logger(__name__)


def _canonical(value: Any) -> Any:
    """Render ``value`` as something :func:`json.dumps` can order and compare.

    Only shapes whose identity is genuinely captured are handled — Pydantic
    models (every Qdrant filter is one) and sets. Anything else is left to
    ``json.dumps``, which raises ``TypeError``; the caller turns that into a
    cache *miss*. Falling back to ``repr`` here would be worse than useless:
    the default ``repr`` carries a memory address, so equal filters would key
    differently within one process, and an object that defines ``__repr__``
    without covering every field could make different filters key the same.

    Args:
        value: Object encountered while canonicalizing the cache key.

    Returns:
        A JSON-encodable stand-in for ``value``.

    Raises:
        TypeError: When the object has no faithful representation.
    """
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, (set, frozenset)):
        return sorted(str(item) for item in value)
    raise TypeError(f"no stable cache representation for {type(value).__name__}")


def _search_cache_key(
    *,
    collection_name: str,
    tenant_id: str | None,
    retrieval_limit: int,
    query_vector: Sequence[float],
    rerank: bool,
    query_text: str | None,
    provider_kwargs: dict[str, Any],
) -> str | None:
    """Key the search cache on every input that can change the result rows.

    The previous key hashed the query vector's **first ten components** and
    omitted the provider kwargs entirely, so two distinct embeddings sharing a
    head collided, and one vector searched with and without a ``document_id``
    restriction shared a single entry for the whole TTL. ``query_text`` matters
    too, but only when ``rerank`` is on: that is the one path where it reorders
    the rows, and folding it in unconditionally would split entries that are
    genuinely identical.

    Args:
        collection_name: Collection being searched.
        tenant_id: Ambient tenant, already enforced on the query itself.
        retrieval_limit: Row count actually requested from the provider.
        query_vector: The full query embedding.
        rerank: Whether the cross-encoder stage will reorder the rows.
        query_text: Question driving that re-ranking stage, if any.
        provider_kwargs: Everything else forwarded to the provider, filters
            included.

    Returns:
        A stable cache key, or ``None`` when an argument has no faithful
        representation — in which case the caller skips the cache rather than
        risk serving another query's rows.
    """
    digest = hashlib.sha256()
    try:
        components = [float(component) for component in query_vector]
        digest.update(struct.pack(f"<{len(components)}d", *components))
        digest.update(
            json.dumps(
                provider_kwargs, sort_keys=True, default=_canonical, ensure_ascii=False
            ).encode("utf-8")
        )
        if rerank and query_text is not None:
            digest.update(query_text.encode("utf-8"))
    except (TypeError, ValueError, struct.error) as exc:
        logger.debug(f"Search cache disabled for this call — unkeyable input: {exc}")
        return None
    fingerprint = digest.hexdigest()[:32]
    return f"{collection_name}:{tenant_id}:{retrieval_limit}:{fingerprint}:rr={rerank}"


class SearchOrchestrator:
    """
    Orchestrates search retrieval and re-ranking phases.
    """

    def __init__(
        self, config: Any, provider: Any, search_cache: Any | None = None
    ) -> None:
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
        **kwargs: Any,
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
            cache_key = _search_cache_key(
                collection_name=collection_name,
                tenant_id=tenant_id,
                retrieval_limit=retrieval_limit,
                query_vector=query_vector,
                rerank=rerank,
                query_text=query_text,
                provider_kwargs=kwargs,
            )

        if cache_key is not None and self.search_cache is not None:
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
                    search_results = await reranker.rerank(
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
