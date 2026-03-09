"""
Storage Mixin for AgentMemory.

This mixin provides the fundamental mechanisms for adding information to
both the rolling short-term (working) memory buffer and the persistent
long-term storage provider.
"""

from core.observability.logging import get_logger
from typing import Any, Dict, List, Optional

from core.memory.types import MemoryItem, MemoryType

logger = get_logger(__name__)


class StorageMixin:
    """
    Extends AgentMemory with atomic storage operations.

    Handles the logic of routing memories to either the volatile
    `_working_memory` (buffer) or the persistent `provider` store,
    ensuring embeddings are generated if an embedder is available.
    """

    provider: Optional[Any]
    embedder: Optional[Any]
    _working_memory: List[MemoryItem]
    _working_memory_embeddings: List[List[float]]
    _working_memory_limit: int

    async def add_memory(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.SHORT_TERM,
        metadata: Optional[Dict] = None,
    ) -> MemoryItem:
        """
        Record a new memory entry.

        Args:
            content: The text content of the memory.
            memory_type: Category of memory (Short-term, Long-term, etc.).
            metadata: Additional context or properties for the memory.

        Returns:
            The created MemoryItem object.
        """
        item = MemoryItem(
            content=content,
            memory_type=memory_type,
            metadata=metadata or {},
        )

        if memory_type == MemoryType.SHORT_TERM or not self.provider:
            await self._add_to_working_memory(item)

        if self.provider and memory_type != MemoryType.SHORT_TERM:
            try:
                await self.provider.add(item)
            except Exception as e:
                logger.error(f"Failed to persist memory: {e}")

        return item

    async def remember(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.SHORT_TERM,
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryItem:
        """
        Synthesize and store a memory with an explicit importance level.

        Args:
            content: The memory text.
            memory_type: The category of memory (Short-term, Long-term, etc.).
            importance: Level of significance (0.0 to 1.0).
            metadata: Optional additional properties.

        Returns:
            The created MemoryItem object.
        """
        metadata = metadata or {}
        metadata["importance"] = importance
        return await self.add_memory(content, memory_type, metadata)

    async def _add_to_working_memory(self, item: MemoryItem) -> None:
        """
        Adds a MemoryItem to the working memory buffer.

        If an embedder is available, it generates and stores the embedding
        for the item's content. Manages the working memory limit by
        evicting the oldest item if the limit is exceeded.

        Args:
            item: The MemoryItem to add to working memory.
        """
        self._working_memory.append(item)

        if self.embedder:
            try:
                embedding = await self.embedder.encode(item.content)
                if hasattr(embedding, "tolist"):
                    embedding = embedding.tolist()
                self._working_memory_embeddings.append(embedding)
            except Exception as e:
                logger.warning(
                    f"Failed to generate embedding for working memory item: {e}"
                )
                self._working_memory_embeddings.append([])
        else:
            self._working_memory_embeddings.append([])

        if len(self._working_memory) > self._working_memory_limit:
            self._working_memory.pop(0)
            self._working_memory_embeddings.pop(0)

    def clear_working_memory(self) -> int:
        """
        Evicts all entries from the transient working memory.

        Returns:
            The number of items cleared from working memory.
        """
        count = len(self._working_memory)
        self._working_memory.clear()
        self._working_memory_embeddings.clear()
        return count

    @property
    def working_memory_size(self) -> int:
        """
        Get the current number of items in working memory.

        Returns:
            Total count of active items.
        """
        return len(self._working_memory)
