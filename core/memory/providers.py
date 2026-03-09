"""
Memory Providers Module.

Contains concrete implementations of the MemoryProvider protocol.
Includes vector-backed storage for long-term persistence and
ephemeral in-memory storage for testing and transient state.
"""

from core.observability.logging import get_logger
from typing import List, Optional
from core.models.domain import Document

from core.services.vectorstore.service import get_vectorstore_service
from .interfaces import MemoryProvider
from .types import MemoryItem, MemoryType

logger = get_logger(__name__)


class VectorMemoryProvider(MemoryProvider):
    """
    Persistent memory store backed by a vector database.

    Integrates with the system's `VectorStoreService` to provide
    high-performance semantic search and long-term archival of
    memories (Episodic and Semantic).
    """

    def __init__(self, collection_name: str = "agent_memory", embedder=None):
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

    async def add(self, item: MemoryItem) -> None:
        """Add an item to vector memory."""
        # Convert MemoryItem to a "Document" format expected by VectorStoreService
        doc = Document(
            id=str(item.id),
            content=item.content,
            metadata={
                **item.metadata,
                "type": item.memory_type.value,
                "created_at": item.created_at.isoformat(),
                "score": 1.0,  # Default score for new items
            },
        )

        # Index it
        try:
            await self.vector_service.index(
                documents=[doc],
                collection_name=self.collection_name,
                embedder=self.embedder,
            )
        except Exception as e:
            logger.error(f"Failed to add memory to vector store: {e}")
            raise e

    async def get(self, item_id: str) -> Optional[MemoryItem]:
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

    async def search(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> List[MemoryItem]:
        """Search for relevant memories semantically."""
        if not self.embedder:
            logger.warning("No embedder configured, cannot perform vector search")
            return []

        # Generate query vector
        query_vector = self.embedder.encode(query)
        if hasattr(query_vector, "tolist"):
            query_vector = query_vector.tolist()

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

                item = MemoryItem(
                    content=doc.content,
                    memory_type=MemoryType(
                        doc.metadata.get("type", MemoryType.LONG_TERM.value)
                    ),
                    metadata=doc.metadata,
                    score=score,
                )
                memory_items.append(item)
            except Exception as e:
                logger.warning(f"Failed to reconstruct memory item: {e}")
                continue

        return memory_items

    async def clear(self, memory_type: Optional[MemoryType] = None) -> None:
        """Clear memories from the vector store."""
        try:
            await self.vector_service.delete_collection(self.collection_name)
            logger.info(f"Cleared vector memory collection: {self.collection_name}")
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


class InMemoryProvider(MemoryProvider):
    """
    Volatile, RAM-only memory store.

    Designed for lightweight ephemeral context, testing environments,
    or scenarios where persistence is explicitly not required.
    """

    def __init__(self):
        self._checkpoints = {}  # Dict[str, MemoryItem]

    async def add(self, item: MemoryItem) -> None:
        """
        Add a memory item to the in-memory store.

        Args:
            item: The MemoryItem to store.
        """
        self._checkpoints[str(item.id)] = item

    async def get(self, item_id: str) -> Optional[MemoryItem]:
        """
        Retrieve a memory item by its ID.

        Args:
            item_id: Unique identifier for the memory item.

        Returns:
            The stored MemoryItem if found, else None.
        """
        return self._checkpoints.get(item_id)

    async def search(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> List[MemoryItem]:
        """
        Search for memory items in the in-memory store by keyword.

        Args:
            query: The text query to search for.
            memory_type: Optional filter by memory category.
            limit: Maximum number of results to return.
            min_score: Minimum relevance score (ignored for in-memory).

        Returns:
            A list of matching MemoryItem objects.
        """
        # Simple keyword match for in-memory
        results = []
        for item in self._checkpoints.values():
            if memory_type and item.memory_type != memory_type:
                continue
            if query.lower() in item.content.lower():
                # Fake score
                item.score = 1.0
                results.append(item)
        return results[:limit]

    async def delete(self, item_id: str) -> bool:
        """Delete a specific memory item by ID."""
        if item_id in self._checkpoints:
            del self._checkpoints[item_id]
            return True
        return False

    async def clear(self, memory_type: Optional[MemoryType] = None) -> None:
        """
        Clear memories from the in-memory store.

        Args:
            memory_type: Optional filter to clear only a specific category.
        """
        if memory_type:
            self._checkpoints = {
                k: v
                for k, v in self._checkpoints.items()
                if v.memory_type != memory_type
            }
        else:
            self._checkpoints.clear()
