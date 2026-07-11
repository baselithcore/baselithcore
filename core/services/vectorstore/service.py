"""
Main VectorStore service implementation.

This module provides a unified interface for vector database operations,
abstracting away specific provider implementations (like Qdrant).
It handles:
1. Multi-tenant data isolation via tenant_id filtering.
2. Automated chunking and embedding generation during indexing.
3. Hybrid search with support for vector similarity and optional re-ranking.
4. Result and embedding caching for performance optimization.
"""

from collections.abc import Sequence
from typing import Any

from core.config import get_vectorstore_config
from core.context import get_current_tenant_id
from core.models.domain import Document, SearchResult
from core.observability.logging import get_logger
from core.optimization.caching import RedisCache
from core.services.vectorstore.embedding_cache import (
    EmbedderProtocol,
)
from core.services.vectorstore.exceptions import VectorStoreError
from core.services.vectorstore.interfaces import VectorStoreProtocol
from core.services.vectorstore.orchestrator import SearchOrchestrator
from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

logger = get_logger(__name__)


class VectorStoreService:
    """
    Coordinator for vector database operations.

    Implements the VectorStoreProtocol, ensuring that all storage and
    retrieval actions respect multi-tenancy and leverage caching where possible.
    """

    def __init__(self, config=None, provider=None):
        """
        Initialize the VectorStore service.

        Args:
            config: VectorStore configuration defaults (uses get_vectorstore_config() if None).
            provider: Explicit provider instance. If None, it is created from config.
        """
        self.config = config or get_vectorstore_config()

        # Initialize Embedding Cache (Redis): avoids re-calculating identical
        # vectors. The TTL bounds Redis memory: without one, every unique
        # chunk leaves a permanent key and memory grows until eviction/OOM.
        # A non-numeric/malformed configured TTL degrades to the default
        # rather than crashing service construction.
        _default_embedding_ttl = 7 * 24 * 3600
        try:
            embedding_ttl = int(
                getattr(self.config, "embedding_cache_ttl", _default_embedding_ttl)
            )
        except (TypeError, ValueError):
            embedding_ttl = _default_embedding_ttl
        self.cache = RedisCache(prefix="embedding", default_ttl=embedding_ttl)

        # Initialize Search Result Cache: improves performance for repeated queries.
        self.search_cache = RedisCache(prefix="search")
        self._search_cache_enabled = getattr(self.config, "search_cache_enabled", True)
        self._search_cache_ttl = getattr(self.config, "search_cache_ttl", 300)

        if provider:
            self.provider = provider
        else:
            self.provider = self._create_provider()

        # Search orchestration delegate
        self.orchestrator = SearchOrchestrator(
            config=self.config, provider=self.provider, search_cache=self.search_cache
        )

        logger.info(
            f"Initialized VectorStoreService with provider={self.config.provider}, "
            f"collection={self.config.collection_name}"
        )

    def _create_provider(self) -> VectorStoreProtocol:
        """
        Instantiate the concrete vector database provider based on configuration.

        Returns:
            VectorStoreProtocol: The active provider (e.g., QdrantProvider).
        """
        if self.config.provider == "qdrant":
            return QdrantProvider(
                host=self.config.host,
                port=self.config.port,
                grpc_port=self.config.grpc_port,
                mode=self.config.qdrant_mode,
                path=self.config.qdrant_path,
            )
        else:
            raise VectorStoreError(f"Unsupported provider: {self.config.provider}")

    async def create_collection(
        self,
        collection_name: str | None = None,
        vector_size: int | None = None,
        **kwargs,
    ) -> None:
        """
        Create a new collection/index in the vector store.

        Args:
            collection_name: Optional override for the target collection.
            vector_size: Dimensionality of the vectors (e.g., 1536 for OpenAI).
            **kwargs: Extra provider-specific options.
        """
        collection_name = collection_name or self.config.collection_name
        vector_size = vector_size or self.config.embedding_dim

        try:
            await self.provider.create_collection(
                collection_name=collection_name, vector_size=vector_size, **kwargs
            )
            logger.info(f"Created collection '{collection_name}'")
        except Exception as e:
            logger.error(f"Failed to create collection: {e}")
            raise VectorStoreError(f"Collection creation failed: {e}") from e

    async def delete_collection(
        self,
        collection_name: str | None = None,
        **kwargs,
    ) -> None:
        """
        Permanently delete a collection.

        Args:
            collection_name: Target collection name.
            **kwargs: Extra provider-specific parameters.
        """
        collection_name = collection_name or self.config.collection_name

        try:
            if hasattr(self.provider, "delete_collection"):
                await self.provider.delete_collection(
                    collection_name=collection_name, **kwargs
                )
                logger.info(f"Deleted collection '{collection_name}' via provider")
            elif hasattr(self.provider, "client") and hasattr(
                self.provider.client, "delete_collection"
            ):
                await self.provider.client.delete_collection(
                    collection_name=collection_name, **kwargs
                )
                logger.info(
                    f"Deleted collection '{collection_name}' via provider's client"
                )
            else:
                logger.warning("Provider does not support deleting collections.")
        except Exception as e:
            logger.error(f"Failed to delete collection: {e}")
            raise VectorStoreError(f"Collection deletion failed: {e}") from e

    async def index(
        self,
        documents: Sequence[Document],
        collection_name: str | None = None,
        embedder: EmbedderProtocol | None = None,
        **kwargs,
    ) -> int:
        """
        Index a sequence of documents into the vector store.

        Orchestrates the full pipeline as a batch (body in ``_indexing`` —
        module size cap): chunking, a single embedding pass with caching,
        multi-tenant metadata enrichment, one bulk upsert. Single-document
        callers keep working unchanged.

        Args:
            documents: List of Document domain models.
            collection_name: Target collection override.
            embedder: Concrete embedder protocol implementation.
            **kwargs: Extra parameters for indexing.

        Returns:
            int: Number of documents successfully indexed.
        """
        from core.services.vectorstore._indexing import index_documents

        return await index_documents(
            self,
            documents,
            collection_name=collection_name,
            embedder=embedder,
            **kwargs,
        )

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
        Perform a vector similarity search with tenant isolation.

        Supports an optional two-stage retrieval process:
        1. Retrieval: Fetch top candidates using vector similarity.
        2. Re-ranking: (Optional) Re-sort candidates using a cross-encoder.

        Args:
            query_vector: Numerical representation of the query.
            k: Number of final results to return.
            collection_name: Target collection.
            use_cache: If True, attempts to retrieve from search result cache.
            query_text: Original query string (needed for re-ranking).
            rerank: If True, applies semantic re-ranking after retrieval.
            **kwargs: Extra parameters (like filtering).

        Returns:
            Sequence[SearchResult]: Ranked list of results.
        """
        return await self.orchestrator.search(
            query_vector=query_vector,
            k=k,
            collection_name=collection_name,
            use_cache=use_cache,
            query_text=query_text,
            rerank=rerank,
            **kwargs,
        )

    async def retrieve(
        self,
        point_ids: list[int | str],
        collection_name: str | None = None,
        **kwargs,
    ) -> list[Any]:
        """
        Directly fetch specific points by their IDs.

        Args:
            point_ids: List of unique point identifiers.
            collection_name: Target collection.
            **kwargs: Extra parameters.

        Returns:
            List[Any]: Raw point objects from the provider.
        """
        collection_name = collection_name or self.config.collection_name
        tenant_id = get_current_tenant_id()
        kwargs["tenant_id"] = tenant_id

        try:
            return await self.provider.retrieve(
                collection_name=collection_name, point_ids=point_ids, **kwargs
            )
        except Exception as e:
            logger.error(f"Point retrieval failed: {e}")
            raise VectorStoreError(f"Retrieve failed: {e}") from e

    async def delete_document(
        self, document_id: str, collection_name: str | None = None, **kwargs
    ) -> None:
        """
        Remove all vector points associated with a specific document ID.

        Enforces tenant isolation during deletion to prevent cross-tenant data corruption.

        Args:
            document_id: External document identifier.
            collection_name: Collection name override.
            **kwargs: Extra filter parameters.
        """
        collection_name = collection_name or self.config.collection_name
        tenant_id = get_current_tenant_id()
        kwargs["tenant_id"] = tenant_id

        try:
            if hasattr(self.provider, "delete_by_filter"):
                await self.provider.delete_by_filter(
                    collection_name=collection_name,
                    key="document_id",
                    value=document_id,
                    **kwargs,
                )
                logger.info(
                    f"Successfully deleted all chunks for document {document_id}"
                )
            else:
                logger.warning(
                    f"Provider lacks efficient filtered deletion. Document {document_id} remains indexed."
                )
        except Exception as e:
            logger.error(f"Document deletion failed for {document_id}: {e}")
            raise VectorStoreError(f"Delete document failed: {e}") from e

    async def scroll(
        self,
        collection_name: str | None = None,
        limit: int = 100,
        offset: int | str | None = None,
        **kwargs,
    ) -> Any:
        """
        Paginate through all points in a collection (Scroll API).

        Args:
            collection_name: Target collection.
            limit: Page size.
            offset: Continuation token for pagination.

        Returns:
            Any: Provider-specific iterator/page object.
        """
        collection_name = collection_name or self.config.collection_name
        kwargs["tenant_id"] = get_current_tenant_id()

        try:
            return await self.provider.scroll(
                collection_name=collection_name, limit=limit, offset=offset, **kwargs
            )
        except Exception as e:
            logger.error(f"Scroll operation in '{collection_name}' failed: {e}")
            raise VectorStoreError(f"Scroll failed: {e}") from e

    async def query_points(
        self,
        query_vector: Sequence[float],
        collection_name: str | None = None,
        limit: int = 10,
        **kwargs,
    ) -> Any:
        """
        Perform a raw low-level query through the provider protocol.

        Args:
            query_vector: Query vector sequence.
            collection_name: Target collection.
            limit: Max results.
            **kwargs: Direct provider-specific arguments (e.g., complex filters).
        """
        collection_name = collection_name or self.config.collection_name
        kwargs.setdefault("tenant_id", get_current_tenant_id())

        try:
            if hasattr(self.provider, "query_points"):
                return await self.provider.query_points(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    limit=limit,
                    **kwargs,
                )
            else:
                raise NotImplementedError(
                    "Provider does not support advanced query_points API."
                )
        except Exception as e:
            logger.error(f"Raw point query failed: {e}")
            raise VectorStoreError(f"Query points failed: {e}") from e

    async def query_points_groups(
        self,
        query_vector: Sequence[float],
        group_by: str,
        collection_name: str | None = None,
        limit: int = 10,
        group_size: int = 1,
        **kwargs,
    ) -> Any:
        """
        Grouped query: best ``group_size`` chunks per top ``limit`` groups.

        One round trip replaces a per-group query fan-out (e.g. best chunk
        per document).

        Args:
            query_vector: Query vector sequence.
            group_by: Payload field to group results by (e.g. 'document_id').
            collection_name: Target collection.
            limit: Max number of groups returned.
            group_size: Chunks returned per group.
            **kwargs: Direct provider-specific arguments (e.g. filters).
        """
        collection_name = collection_name or self.config.collection_name
        kwargs.setdefault("tenant_id", get_current_tenant_id())

        try:
            if hasattr(self.provider, "query_points_groups"):
                return await self.provider.query_points_groups(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    group_by=group_by,
                    limit=limit,
                    group_size=group_size,
                    **kwargs,
                )
            else:
                raise NotImplementedError(
                    "Provider does not support grouped query API."
                )
        except Exception as e:
            logger.error(f"Grouped point query failed: {e}")
            raise VectorStoreError(f"Query points groups failed: {e}") from e


# Global singleton instance.
_vectorstore_service: VectorStoreService | None = None


def get_vectorstore_service() -> VectorStoreService:
    """
    Retrieve or initialize the global VectorStoreService instance.

    Returns:
        VectorStoreService: The shared service instance.
    """
    global _vectorstore_service
    if _vectorstore_service is None:
        _vectorstore_service = VectorStoreService()
    return _vectorstore_service
