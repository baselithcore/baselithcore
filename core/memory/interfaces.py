"""
Memory Interfaces Module.

This module defines the core protocols and abstract base classes for
BaselithCore's memory system. It establishes a vendor-agnostic interface
for storage backends (MemoryProvider) and context synthesis (ContextProvider).
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Protocol

from .types import MemoryItem, MemoryType


class MemoryProvider(Protocol):
    """
    Standard protocol for memory storage and retrieval.

    Implementing classes must handle the lifecycle of memory items,
    including semantic search and categorization by MemoryType.
    """

    async def add(self, item: MemoryItem) -> None:
        """Add an item to memory."""
        ...

    async def get(self, item_id: str) -> Optional[MemoryItem]:
        """Retrieve a specific memory item."""
        ...

    async def delete(self, item_id: str) -> bool:
        """Delete a specific memory item."""
        ...

    async def search(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> List[MemoryItem]:
        """Search for relevant memories."""
        ...

    async def clear(self, memory_type: Optional[MemoryType] = None) -> None:
        """Clear memories, optionally filtering by type."""
        ...


class ContextProvider(ABC):
    """
    Abstract base class for high-level context construction.

    Translates raw memories into structured strings suitable for LLM prompts.
    """

    @abstractmethod
    async def get_context(self, query: str, **kwargs) -> str:
        """Retrieve relevant context string for a query."""
        pass
