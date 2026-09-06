"""
Vector Store interface definitions.
"""

from collections.abc import Sequence
from typing import Any, Protocol


class VectorStoreProtocol(Protocol):
    """Protocol for vector store providers."""

    async def create_collection(
        self, collection_name: str, vector_size: int, **kwargs: Any
    ) -> None:
        """Create a collection."""
        ...

    async def upsert(
        self, collection_name: str, points: list[dict[str, Any]], **kwargs: Any
    ) -> None:
        """Upsert points."""
        ...

    async def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int = 10,
        **kwargs: Any,
    ) -> list[Any]:
        """Search for similar vectors."""
        ...

    async def retrieve(
        self, collection_name: str, point_ids: list[int | str], **kwargs: Any
    ) -> list[Any]:
        """Retrieve points by ID."""
        ...

    async def delete(
        self, collection_name: str, point_ids: list[int | str], **kwargs: Any
    ) -> None:
        """Delete points."""
        ...

    async def scroll(
        self,
        collection_name: str,
        limit: int = 100,
        offset: int | str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Scroll through points."""
        ...

    async def delete_by_filter(
        self, collection_name: str, key: str, value: Any, **kwargs: Any
    ) -> None:
        """Delete points by filter."""
        ...
