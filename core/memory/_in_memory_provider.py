"""Volatile, RAM-only memory provider.

Split from :mod:`core.memory.providers` for the module size cap. Re-exported
there, so ``from core.memory.providers import InMemoryProvider`` keeps working.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

from .interfaces import MemoryProvider
from .types import MemoryItem, MemoryType

logger = get_logger(__name__)

__all__ = ["InMemoryProvider"]


class InMemoryProvider(MemoryProvider):
    """
    Volatile, RAM-only memory store.

    Designed for lightweight ephemeral context, testing environments,
    or scenarios where persistence is explicitly not required.
    """

    def __init__(self) -> None:
        self._checkpoints: dict[str, MemoryItem] = {}

    async def add(self, item: MemoryItem) -> None:
        """
        Add a memory item to the in-memory store.

        Args:
            item: The MemoryItem to store.
        """
        self._checkpoints[str(item.id)] = item

    async def get(self, item_id: str) -> MemoryItem | None:
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
        memory_type: MemoryType | None = None,
        limit: int = 5,
        min_score: float = 0.0,
        query_vector: list[float] | None = None,
    ) -> list[MemoryItem]:
        """
        Search for memory items in the in-memory store by keyword.

        Args:
            query: The text query to search for.
            memory_type: Optional filter by memory category.
            limit: Maximum number of results to return.
            min_score: Minimum relevance score (ignored for in-memory).
            query_vector: Precomputed embedding; ignored (this store matches by
                keyword, not vector similarity).

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

    async def list_items(
        self, limit: int = 100, offset: Any | None = None
    ) -> tuple[list[MemoryItem], Any]:
        """Enumerate stored memories in insertion order, one page at a time.

        Args:
            limit: Page size.
            offset: Index of the first item to return, or ``None`` to start.

        Returns:
            The page and the index to resume from (``None`` when exhausted).
        """
        start = int(offset or 0)
        items = list(self._checkpoints.values())[start : start + limit]
        next_offset = start + len(items)
        return items, (next_offset if next_offset < len(self._checkpoints) else None)

    async def delete(self, item_id: str) -> bool:
        """Delete a specific memory item by ID."""
        if item_id in self._checkpoints:
            del self._checkpoints[item_id]
            return True
        return False

    async def clear(self, memory_type: MemoryType | None = None) -> None:
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
