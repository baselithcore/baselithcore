"""
Memory Providers Module.

Contains concrete implementations of the MemoryProvider protocol.
Includes vector-backed storage for long-term persistence and
ephemeral in-memory storage for testing and transient state.
"""

import asyncio
import inspect
from typing import Any, cast
from uuid import UUID

from core.context import get_current_tenant_id
from core.models.domain import Document
from core.observability.logging import get_logger
from core.services.vectorstore.service import get_vectorstore_service

from ._in_memory_provider import InMemoryProvider
from .interfaces import MemoryProvider
from .types import MemoryItem, MemoryType

logger = get_logger(__name__)


def _stored_id(document_id: str) -> UUID | None:
    """Recover a ``MemoryItem`` id from the id the vector store round-tripped.

    Items are written with ``Document.id = str(item.id)``, so the identity
    survives the store and comes back on the payload's ``document_id``.
    Reconstruction used to drop it and let ``MemoryItem`` mint a fresh uuid,
    which made every read-back unaddressable: a delete keyed on the recalled
    item's id referred to a row that had never existed, so compression removed
    nothing and its summaries accumulated next to the originals it believed it
    had replaced.

    Args:
        document_id: The id the store returned.

    Returns:
        The parsed uuid, or ``None`` for an id this store did not write.
    """
    try:
        return UUID(document_id)
    except (ValueError, AttributeError, TypeError):
        return None


def _memory_item_from(doc: Document, score: float) -> MemoryItem:
    """Rebuild a :class:`MemoryItem` from a stored document.

    Args:
        doc: The document the vector store returned.
        score: Its similarity score.

    Returns:
        The item, carrying its stored identity when the store round-tripped
        one.
    """
    item = MemoryItem(
        content=doc.content,
        memory_type=MemoryType(doc.metadata.get("type", MemoryType.LONG_TERM.value)),
        metadata=doc.metadata,
        score=score,
    )
    stored = _stored_id(doc.id)
    if stored is not None:
        item.id = stored
    return item


def build_memory_provider(collection: str) -> MemoryProvider | None:
    """Build the persistent backing store for agent memory, or ``None``.

    The single construction site for memory persistence, so the lazy-registry
    bootstrap and the process-wide singleton cannot disagree about whether
    memories survive a restart. Both used to be built with no provider at all:
    the long-term tier fell back to a bounded in-process deque searched by
    substring, so "long-term memory" meant a few hundred items that died with
    the worker and were invisible to every other one.

    Cheap to call: the vector client is constructed here, but the embedder is
    resolved on first query (see
    :meth:`VectorMemoryProvider._resolve_embedder`), so nothing loads a model
    on the calling thread.

    Args:
        collection: Vector-store collection to keep this memory in.

    Returns:
        A provider, or ``None`` when persistence is off or unavailable — in
        which case the reason is logged, naming the consequence.
    """
    from core.config.memory import get_memory_runtime_config

    if not get_memory_runtime_config().persistence_enabled:
        logger.warning(
            "memory_persistence_disabled",
            extra={"consequence": "memories are lost when this process exits"},
        )
        return None

    try:
        return VectorMemoryProvider(collection_name=collection)
    except Exception as exc:
        logger.warning(
            "memory_persistence_unavailable",
            extra={
                "error": str(exc),
                "consequence": "memories are lost when this process exits",
            },
        )
        return None


class VectorMemoryProvider(MemoryProvider):
    """
    Persistent memory store backed by a vector database.

    Integrates with the system's `VectorStoreService` to provide
    high-performance semantic search and long-term archival of
    memories (Episodic and Semantic).
    """

    def __init__(
        self, collection_name: str = "agent_memory", embedder: Any | None = None
    ) -> None:
        """
        Initialize the provider.

        Args:
            collection_name: Name of the vector collection to use
            embedder: Embedder instance (generic) to generate vectors.
                      Must have an `encode(text)` method.
                      If None, will attempt to load a default one.
        """
        self.vector_service = get_vectorstore_service()
        self.collection_name = collection_name
        self.embedder = embedder

        # Collection creation is now handled asynchronously or assumed to exist.
        # Removing sync call from __init__.
        pass

    @staticmethod
    def _to_document(item: MemoryItem) -> Document:
        """Convert a MemoryItem into the Document shape the indexer expects."""
        return Document(
            id=str(item.id),
            content=item.content,
            metadata={
                **item.metadata,
                "type": item.memory_type.value,
                "created_at": item.created_at.isoformat(),
                "score": 1.0,  # Default score for new items
            },
        )

    async def add(self, item: MemoryItem) -> None:
        """Add an item to vector memory."""
        await self.add_many([item])

    async def add_many(self, items: list[MemoryItem]) -> None:
        """Add several items in one embedding pass and one upsert.

        Consolidation and compression write whole batches; routing each item
        through :meth:`add` paid a separate embedding call and a separate
        ``wait=True`` upsert per item, so the durability ack was amortized over
        nothing. ``index`` already handles a batch end to end.
        """
        if not items:
            return
        # Resolve rather than demand: the vector store owns the "an embedder is
        # required" error, and refusing here would turn a loud, specific
        # failure into a silent dropped write.
        embedder = await self._resolve_embedder()
        try:
            await self.vector_service.index(
                documents=[self._to_document(item) for item in items],
                collection_name=self.collection_name,
                embedder=embedder,
            )
        except Exception as e:
            logger.error(f"Failed to add memory to vector store: {e}")
            raise e

    async def get(self, item_id: str) -> MemoryItem | None:
        """
        Retrieve a specific memory item by its ID.
        """
        try:
            results = await self.vector_service.retrieve(
                point_ids=[item_id], collection_name=self.collection_name
            )
            if not results:
                return None

            # Reconstruct MemoryItem from the first found chunk/point
            res = results[0]
            # Since retrieve returns raw provider objects (Record), usage depends on provider.
            # Qdrant Record has .payload
            payload = getattr(res, "payload", {}) or {}

            return MemoryItem(
                content=payload.get("text", ""),
                memory_type=MemoryType(payload.get("type", MemoryType.LONG_TERM.value)),
                metadata=payload,
                score=getattr(res, "score", 1.0),
            )
        except Exception as e:
            logger.error(f"Failed to retrieve memory {item_id}: {e}")
            return None

    async def get_many(self, item_ids: list[str]) -> list[MemoryItem]:
        """
        Retrieve multiple memory items by their IDs in a single batch operation.

        Optimized for batch retrieval - significantly faster than calling get() in a loop.
        Expected performance gain: 60-70% reduction in retrieval time for 10+ items.

        Args:
            item_ids: List of item IDs to retrieve

        Returns:
            List of MemoryItems found (may be shorter than input if some IDs don't exist)
        """
        if not item_ids:
            return []

        try:
            # Batch retrieve all items in one call
            results = await self.vector_service.retrieve(
                point_ids=cast(list[int | str], item_ids),
                collection_name=self.collection_name,
            )

            if not results:
                return []

            # Reconstruct MemoryItems from results
            memory_items = []
            for res in results:
                payload = getattr(res, "payload", {}) or {}
                memory_items.append(
                    MemoryItem(
                        content=payload.get("text", ""),
                        memory_type=MemoryType(
                            payload.get("type", MemoryType.LONG_TERM.value)
                        ),
                        metadata=payload,
                        score=getattr(res, "score", 1.0),
                    )
                )

            logger.debug(
                f"Batch retrieved {len(memory_items)} items from {len(item_ids)} requested IDs"
            )
            return memory_items

        except Exception as e:
            logger.error(f"Failed to batch retrieve memory items: {e}")
            return []

    async def _resolve_embedder(self) -> Any | None:
        """The embedder, loading the default one on first use if none was given.

        Constructing it loads a sentence-transformer model, which is slow and
        synchronous. Deferring it to the first query that actually needs one
        keeps the provider cheap to build, so a *synchronous* construction site
        — ``core.memory.get_memory()`` — can wire persistence without loading a
        model on whatever thread happened to call it.

        Returns:
            The embedder, or ``None`` when one cannot be built.
        """
        if self.embedder is not None:
            return self.embedder

        def _load() -> Any:
            from core.nlp.models import get_embedder

            return get_embedder()

        try:
            self.embedder = await asyncio.to_thread(_load)
        except Exception as e:
            logger.warning(f"Could not load the default embedder: {e}")
            return None
        return self.embedder

    async def search(
        self,
        query: str,
        memory_type: MemoryType | None = None,
        limit: int = 5,
        min_score: float = 0.0,
        query_vector: list[float] | None = None,
    ) -> list[MemoryItem]:
        """Search for relevant memories semantically.

        When ``query_vector`` is supplied the encode step is skipped — the recall
        hot path embeds the query once and reuses the vector across memory tiers.
        """
        if query_vector is None:
            embedder = await self._resolve_embedder()
            if embedder is None:
                logger.warning("No embedder configured, cannot perform vector search")
                return []

            # Generate query vector. Await an async embedder; otherwise offload
            # the blocking sync encode to a thread so we never stall the loop.
            if inspect.iscoroutinefunction(embedder.encode):
                encoded = await embedder.encode(query)
            else:
                encoded = await asyncio.to_thread(embedder.encode, query)
            query_vector = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        assert query_vector is not None

        try:
            results = await self.vector_service.search(
                query_vector=query_vector,
                k=limit,
                collection_name=self.collection_name,
            )
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []

        # Convert results back to MemoryItems
        memory_items = []
        for res in results:
            # res is SearchResult
            try:
                # SearchResult has .document and .score
                doc = res.document
                score = res.score

                if score < min_score:
                    continue

                if memory_type:
                    # Check type in metadata
                    type_val = doc.metadata.get("type")
                    if type_val and type_val != memory_type.value:
                        continue

                memory_items.append(_memory_item_from(doc, score))
            except Exception as e:
                logger.warning(f"Failed to reconstruct memory item: {e}")
                continue

        return memory_items

    async def list_items(
        self, limit: int = 100, offset: Any | None = None
    ) -> tuple[list[MemoryItem], Any]:
        """Enumerate stored memories in provider order, one page at a time.

        This is the deterministic counterpart to :meth:`search`. Maintenance
        work — compaction, retention — needs to know *which* items it is
        acting on; a similarity query cannot answer that, because it ranks by
        distance from some query vector and silently returns a different slice
        each time the corpus changes. Compaction used to call ``search("")``
        and treat the nearest neighbours of the empty string's embedding as
        "the old memories".

        Args:
            limit: Page size.
            offset: Continuation token from the previous page, or ``None``.

        Returns:
            The page and the token for the next one (``None`` when exhausted).
        """
        try:
            page = await self.vector_service.scroll(
                collection_name=self.collection_name, limit=limit, offset=offset
            )
        except Exception as e:
            logger.error(f"Failed to enumerate vector memory: {e}")
            return [], None

        points, next_offset = page if isinstance(page, tuple) else (page, None)
        items: list[MemoryItem] = []
        for point in points or []:
            payload = getattr(point, "payload", {}) or {}
            document = Document(
                id=payload.get("document_id", str(getattr(point, "id", ""))),
                content=payload.get("text", ""),
                metadata=payload,
            )
            if not document.content:
                continue
            items.append(_memory_item_from(document, score=1.0))
        return items, next_offset

    async def clear(self, memory_type: MemoryType | None = None) -> None:
        """Clear the current tenant's memories (optionally of one type).

        The collection is shared by every tenant, so this is a tenant-scoped
        filtered delete — never ``delete_collection``, which used to wipe all
        tenants' memories and ignored ``memory_type``. With a type, only
        points whose ``type`` payload matches are removed; without one, every
        point owned by the ambient tenant is.

        A backend without filtered deletion leaves the memories in place and
        logs a warning: refusing is the only safe answer on a shared
        collection.

        Args:
            memory_type: Restrict the delete to this memory type.
        """
        tenant_id = get_current_tenant_id()
        backend = getattr(self.vector_service, "provider", None)
        delete_by_filter = getattr(backend, "delete_by_filter", None)
        if delete_by_filter is None:
            logger.warning(
                "vector_memory_clear_unsupported",
                extra={"collection": self.collection_name, "tenant_id": tenant_id},
            )
            return
        if memory_type is not None:
            key, value = "type", memory_type.value
        else:
            key, value = "tenant_id", tenant_id
        try:
            await delete_by_filter(
                collection_name=self.collection_name,
                key=key,
                value=value,
                tenant_id=tenant_id,
            )
            logger.info(
                f"Cleared vector memory in {self.collection_name} "
                f"(tenant={tenant_id}, type={memory_type.value if memory_type else '*'})"
            )
        except Exception as e:
            logger.error(f"Failed to clear vector memory: {e}")

    async def delete(self, item_id: str) -> bool:
        """Delete a specific memory item by ID."""
        try:
            await self.vector_service.delete_document(
                item_id, collection_name=self.collection_name
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete memory {item_id}: {e}")
            return False

    async def delete_many(self, item_ids: list[str]) -> None:
        """Delete a batch of memory items in one filtered round-trip.

        Compaction rewrites whole batches; routing each id through
        :meth:`delete` paid one vector-store round-trip per item.
        """
        if not item_ids:
            return
        try:
            await self.vector_service.delete_documents(
                list(item_ids), collection_name=self.collection_name
            )
        except Exception as e:
            logger.error(f"Failed to batch-delete {len(item_ids)} memories: {e}")
            raise


__all__ = [
    "InMemoryProvider",
    "VectorMemoryProvider",
    "build_memory_provider",
]
