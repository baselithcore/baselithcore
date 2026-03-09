"""
Document Indexing Service implementation.

Replaces legacy core/vectorstore/indexing.py with DI-based approach.
"""

from __future__ import annotations

import json
from core.observability.logging import get_logger
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from core.config import get_vectorstore_config, get_processing_config
from core.services.vectorstore import get_vectorstore_service
from core.models.domain import Document
from core.nlp import get_embedder
from core.observability.metrics import (
    INDEXED_DOCUMENTS_GAUGE,
    INDEXED_DOCUMENTS_TOTAL,
    INDEXING_DURATION_SECONDS,
    INDEXING_RUNS_TOTAL,
)
from core.observability import telemetry

logger = get_logger(__name__)


@dataclass
class IndexingStats:
    """Statistics from an indexing run."""

    new_documents: int = 0
    skipped_documents: int = 0
    deleted_documents: int = 0
    graph_writes: int = 0
    duration_seconds: float = 0.0
    per_origin: Dict[str, int] = field(default_factory=dict)


@dataclass
class IndexedDocument:
    """Tracked state of an indexed document."""

    fingerprint: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    mtime: Optional[float] = None
    size: Optional[int] = None


class IndexingService:
    """
    Document indexing service.

    Provides incremental document indexing with:
    - Document source abstraction
    - Fingerprint-based change detection
    - VectorStore integration via DI
    - Optional GraphDB synchronization

    Example:
        ```python
        service = get_indexing_service()
        stats = await service.index_documents(incremental=True)
        print(f"Indexed {stats.new_documents} documents")
        ```
    """

    def __init__(
        self,
        vectorstore_service=None,
        embedder=None,
        config=None,
    ):
        """
        Initialize IndexingService.

        Args:
            vectorstore_service: VectorStore service instance (uses global if None)
            embedder: Embedder instance (uses config default if None)
            config: VectorStore config (uses get_vectorstore_config() if None)
        """
        self._config = config or get_vectorstore_config()
        self._proc_config = get_processing_config()
        self._vectorstore = vectorstore_service or get_vectorstore_service()

        if embedder:
            self._embedder = embedder
        else:
            self._embedder = get_embedder(self._config.embedding_model)

        # In-memory state of indexed documents
        self._indexed_items: Dict[str, IndexedDocument] = {}
        self._state_loaded = False
        self._redis_state_key = "baselith:indexing:state"
        self._redis = None

        logger.info(
            "IndexingService initialized with embedder=%s",
            self._config.embedding_model,
        )

    @property
    def indexed_documents(self) -> Dict[str, IndexedDocument]:
        """
        Access the current registry of indexed documents.

        Returns:
            Dict[str, IndexedDocument]: Map of document IDs to their state.
        """
        return self._indexed_items

    @property
    def indexed_count(self) -> int:
        """
        Calculate the total number of documents in the index.

        Returns:
            int: The size of the index state.
        """
        return len(self._indexed_items)

    async def index_documents(
        self,
        incremental: bool = True,
        sources: Optional[List[Any]] = None,
    ) -> IndexingStats:
        """
        Index documents from configured sources.

        Args:
            incremental: If True, skip unchanged documents
            sources: Optional list of document sources (uses config if None)

        Returns:
            IndexingStats with results
        """
        start_time = time.perf_counter()
        stats = IndexingStats()

        # Load persisted state on first run
        await self._load_state()

        # Import here to avoid circular deps
        from core.doc_sources import DocumentSourceError, create_document_sources

        # Get document sources
        if sources is None:
            try:
                sources_with_name = create_document_sources()
            except DocumentSourceError as exc:
                raise RuntimeError(
                    f"Invalid document source configuration: {exc}"
                ) from exc
        else:
            sources_with_name = [(s.__class__.__name__, s) for s in sources]

        if not sources_with_name:
            logger.warning("[indexing] No active document sources")
            return stats

        current_document_ids: Set[str] = set()

        try:
            # Process each source
            for source_name, source in sources_with_name:
                source_stats = await self._process_source(
                    source_name,
                    source,
                    incremental,
                    current_document_ids,
                )
                stats.new_documents += source_stats.new_documents
                stats.skipped_documents += source_stats.skipped_documents
                stats.graph_writes += source_stats.graph_writes
                stats.per_origin[source_name] = source_stats.new_documents

        finally:
            # Close sources
            for source_name, source in sources_with_name:
                close = getattr(source, "close", None)
                if callable(close):
                    try:
                        import inspect

                        if inspect.iscoroutinefunction(close):
                            await close()
                        else:
                            close()
                    except Exception as e:
                        logger.warning(f"Error closing source {source_name}: {e}")

        # Clean up stale documents
        stale_ids = set(self._indexed_items.keys()) - current_document_ids
        if stale_ids:
            stats.deleted_documents = await self._delete_stale_documents(stale_ids)

        # Persist state to Redis
        await self._save_state()

        # Record metrics
        stats.duration_seconds = time.perf_counter() - start_time
        self._record_metrics(stats, incremental)

        logger.info(
            "[indexing] Completed: new=%d, skipped=%d, deleted=%d, duration=%.2fs",
            stats.new_documents,
            stats.skipped_documents,
            stats.deleted_documents,
            stats.duration_seconds,
        )

        return stats

    async def ingest_file(
        self,
        file_path: str,
        collection: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IndexingStats:
        """
        Ingest a single file.

        Args:
            file_path: Path to the document file
            collection: Target collection name
            metadata: Optional document metadata

        Returns:
            IndexingStats with results
        """
        from core.doc_sources.filesystem import FilesystemDocumentSource
        from pathlib import Path

        path = Path(file_path)
        # Use parent as root to satisfy security checks in FilesystemDocumentSource
        source = FilesystemDocumentSource(root=path.parent)

        item = await source.read_item(path)
        stats = IndexingStats()

        if item:
            if metadata:
                item.metadata.update(metadata)

            await self._index_document(item)
            stats.new_documents = 1

            # Update tracked state
            self._indexed_items[item.uid] = IndexedDocument(
                fingerprint=item.fingerprint,
                metadata=dict(item.metadata or {}),
            )

        return stats

    async def reindex_collection(
        self,
        collection_name: str,
        force: bool = False,
    ) -> IndexingStats:
        """
        Reindex a collection.

        Args:
            collection_name: Collection to reindex
            force: If True, reindex even if unchanged

        Returns:
            IndexingStats with results
        """
        # For now, this is equivalent to full indexing of active sources
        return await self.index_documents(incremental=not force)

    async def _process_source(
        self,
        source_name: str,
        source: Any,
        incremental: bool,
        current_document_ids: Set[str],
    ) -> IndexingStats:
        """
        Ingest and index all items from a specific document source.

        Args:
            source_name: Identifier for logs/metrics.
            source: The DocumentSource instance.
            incremental: If True, check fingerprints before indexing.
            current_document_ids: Set to populate with discovered IDs.

        Returns:
            IndexingStats: Results for this specific source.
        """
        stats = IndexingStats()

        logger.info(f"[indexing] Processing source: {source_name}")

        # Use async iteration
        async for item in self._iter_source_items(source):
            current_document_ids.add(item.uid)

            # Check if document changed
            if incremental:
                prev = self._indexed_items.get(item.uid)
                if prev and prev.fingerprint == item.fingerprint:
                    stats.skipped_documents += 1
                    continue

            # Index the document
            try:
                await self._index_document(item)
                stats.new_documents += 1

                # Update tracked state
                self._indexed_items[item.uid] = IndexedDocument(
                    fingerprint=item.fingerprint,
                    metadata=dict(item.metadata or {}),
                )
            except Exception as e:
                logger.error(f"[indexing] Failed to index {item.uid}: {e}")

        return stats

    async def _iter_source_items(self, source):
        """
        Safely iterate over items from a source, supporting sync and async iterators.

        Args:
            source: The source to iterate.

        Yields:
            DocumentItem: Raw document items from the source.
        """
        import inspect

        items = source.iter_items()
        if inspect.isawaitable(items):
            items = await items

        if hasattr(items, "__aiter__"):
            async for item in items:
                yield item
        else:
            for item in items:
                yield item

    async def _index_document(self, item) -> None:
        """
        Transform a raw source item into a domain Document and index it.

        Args:
            item: The raw item from the document source.
        """

        content = item.content
        if not content:
            return

        # Create Domain Document
        try:
            doc = Document(
                id=item.uid,
                content=content,
                metadata=item.metadata or {},
            )
            # Add source info to metadata if not present
            if "source" not in doc.metadata:
                doc.metadata["source"] = getattr(item, "clean_path", item.uid)

            # Delegate to vectorstore service
            await self._vectorstore.index(
                documents=[doc],
                collection_name=self._config.collection_name,
                embedder=self._embedder,
            )

        except Exception as e:
            logger.error(f"Error during vectorstore indexing for {item.uid}: {e}")
            raise

    async def _delete_stale_documents(self, stale_ids: Set[str]) -> int:
        """
        Remove documents from the vector store that are no longer in the sources.

        Args:
            stale_ids: Set of document IDs to remove.

        Returns:
            int: Count of successfully deleted documents.
        """
        deleted = 0

        for doc_id in stale_ids:
            try:
                await self._vectorstore.delete_document(doc_id)
                self._indexed_items.pop(doc_id, None)
                deleted += 1
            except Exception as e:
                logger.warning(f"[indexing] Failed to delete {doc_id}: {e}")

        if deleted:
            logger.info(f"[indexing] Deleted {deleted} stale documents")

        return deleted

    def _get_redis_client(self):
        """
        Initialize and retrieve the Redis client for state persistence.

        Returns:
            Optional[Redis]: The redis client or None if unavailable/disabled.
        """
        if self._redis:
            return self._redis

        try:
            from core.cache import create_redis_client
            from core.config import get_storage_config

            config = get_storage_config()
            if not config.cache_redis_url:
                logger.warning(
                    "[indexing] No Redis URL configured for state persistence"
                )
                return None

            self._redis = create_redis_client(config.cache_redis_url)
            return self._redis
        except Exception as e:
            logger.error(f"[indexing] Failed to initialize Redis client: {e}")
            return None

    async def close(self) -> None:
        """Close resources held by the service."""
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception as e:
                logger.warning(f"Error closing Redis client: {e}")
            finally:
                self._redis = None

    async def _load_state(self) -> None:
        """
        Fetch the previous indexing state from the persistence layer.
        """
        if self._state_loaded:
            return

        self._state_loaded = True
        redis = self._get_redis_client()
        if redis is None:
            return

        try:
            data = await redis.get(self._redis_state_key)
            if data:
                state = json.loads(data)
                for uid, doc_data in state.items():
                    self._indexed_items[uid] = IndexedDocument(
                        fingerprint=doc_data["fingerprint"],
                        metadata=doc_data.get("metadata", {}),
                    )
                logger.info(
                    "[indexing] Loaded %d document states from Redis",
                    len(self._indexed_items),
                )
        except Exception as e:
            logger.warning(f"[indexing] Failed to load state from Redis: {e}")

    async def _save_state(self) -> None:
        """
        Persist the current indexing state to the persistence layer.
        """
        redis = self._get_redis_client()
        if redis is None:
            return

        try:
            state = {
                uid: {
                    "fingerprint": doc.fingerprint,
                    "metadata": doc.metadata,
                }
                for uid, doc in self._indexed_items.items()
            }
            await redis.set(self._redis_state_key, json.dumps(state))
            logger.debug(
                "[indexing] Saved %d document states to Redis",
                len(self._indexed_items),
            )
        except Exception as e:
            logger.warning(f"[indexing] Failed to save state to Redis: {e}")

    def _record_metrics(self, stats: IndexingStats, incremental: bool) -> None:
        """
        Update system metrics with the results of an indexing run.

        Args:
            stats: The calculated statistics.
            incremental: Whether the run was incremental or full.
        """
        mode_label = "incremental" if incremental else "full"

        INDEXING_RUNS_TOTAL.labels(mode=mode_label).inc()
        INDEXING_DURATION_SECONDS.labels(mode=mode_label).observe(
            stats.duration_seconds
        )

        if stats.new_documents > 0:
            telemetry.increment("indexing.new_documents", value=stats.new_documents)
            INDEXED_DOCUMENTS_TOTAL.inc(stats.new_documents)

        INDEXED_DOCUMENTS_GAUGE.set(self.indexed_count)


# Global instance
_indexing_service: Optional[IndexingService] = None


def get_indexing_service() -> IndexingService:
    """Get or create the global IndexingService instance."""
    global _indexing_service
    if _indexing_service is None:
        _indexing_service = IndexingService()
    return _indexing_service
