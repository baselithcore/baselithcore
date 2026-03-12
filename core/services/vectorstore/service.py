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

from core.observability.logging import get_logger
from typing import List, Any, Sequence, Optional

from core.optimization.caching import RedisCache
from core.services.vectorstore.embedding_cache import (
    EmbedderProtocol,
    get_embeddings_cached,
)
from core.config import get_vectorstore_config
from core.context import get_current_tenant_id
from core.services.vectorstore.chunking import (
    chunk_text,
    prepare_chunk_text,
    chunk_point_id,
)
from core.services.vectorstore.interfaces import VectorStoreProtocol
from core.services.vectorstore.exceptions import VectorStoreError
from core.services.vectorstore.providers.qdrant_provider import QdrantProvider
from core.models.domain import Document, SearchResult

from core.services.vectorstore.orchestrator import SearchOrchestrator

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

        # Initialize Embedding Cache (Redis): avoids re-calculating identical vectors.
        self.cache = RedisCache(prefix="embedding")

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
        embedder: Optional[EmbedderProtocol] = None,
        **kwargs,
    ) -> int:
        """
        Index a sequence of documents into the vector store.

        Orchestrates the full pipeline:
        1. Document chunking.
        2. Embedding generation (with caching).
        3. Multi-tenant metadata enrichment.
        4. Upserting points to the store.

        Args:
            documents: List of Document domain models.
            collection_name: Target collection override.
            embedder: Concrete embedder protocol implementation.
            **kwargs: Extra parameters for indexing.

        Returns:
            int: Number of items successfully indexed.
        """
        collection_name = collection_name or self.config.collection_name

        if not embedder:
            raise VectorStoreError("Embedder is required for indexing documents")

        indexed_count = 0

        for doc in documents:
            doc_id = doc.id
            content = doc.content
            metadata = doc.metadata

            if not doc_id or not content:
                logger.warning("Skipping document with missing id or content")
                continue

            # 1. Chunking logic: Split long text into manageable overlapping segments.
            chunks = chunk_text(content)
            if not chunks:
                logger.warning(f"No chunks generated for document {doc_id}")
                continue

            # 2. Preparation: Clean and format chunks for embedding.
            enriched_chunks = [prepare_chunk_text(chunk, metadata) for chunk in chunks]

            # 3. Embedding: Convert text to vectors (leveraging Redis cache).
            try:
                vectors = await get_embeddings_cached(
                    embedder,
                    enriched_chunks,
                    self.cache,
                    model_id=self.config.embedding_model,
                )
            except Exception as e:
                logger.error(f"Failed to generate embeddings for {doc_id}: {e}")
                continue

            # 4. Point Creation: Map chunks and vectors to store-native point format.
            points = []
            current_tenant = get_current_tenant_id()
            for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
                payload = {
                    "text": chunk,
                    "source": getattr(doc, "clean_path", doc.id),
                    "document_id": doc_id,
                    "tenant_id": current_tenant,
                    "chunk_index": idx,
                    "chunk_count": len(chunks),
                }
                payload.update(metadata)

                points.append(
                    {
                        "id": chunk_point_id(doc_id, idx),
                        "vector": vector,
                        "payload": payload,
                    }
                )

            # 5. Native Upsert: Push points to the provider.
            try:
                await self.provider.upsert(
                    collection_name=collection_name, points=points, **kwargs
                )
                indexed_count += 1
                logger.debug(f"Indexed document {doc_id} with {len(chunks)} chunks")
            except Exception as e:
                logger.error(f"Failed to index document {doc_id}: {e}")
                continue

        logger.info(
            f"Indexing complete: {indexed_count}/{len(documents)} documents processed"
        )
        return indexed_count

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
        point_ids: List[int | str],
        collection_name: str | None = None,
        **kwargs,
    ) -> List[Any]:
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
        Perform a raw low-level query directly against the provider client.

        Note: Tenant isolation must be manually handled in kwargs if needed.

        Args:
            query_vector: Query vector sequence.
            collection_name: Target collection.
            limit: Max results.
            **kwargs: Direct provider-specific arguments (e.g., complex filters).
        """
        collection_name = collection_name or self.config.collection_name

        try:
            if hasattr(self.provider.client, "query_points"):
                return await self.provider.client.query_points(
                    collection_name=collection_name,
                    query=list(query_vector),
                    limit=limit,
                    **kwargs,
                )
            else:
                raise NotImplementedError(
                    "Provider client does not support advanced query_points API."
                )
        except Exception as e:
            logger.error(f"Raw point query failed: {e}")
            raise VectorStoreError(f"Query points failed: {e}") from e


# Global singleton instance.
_vectorstore_service: Optional[VectorStoreService] = None


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
