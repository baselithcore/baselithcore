"""
Base provider interface for vector store providers.
"""

from typing import Protocol, List, Dict, Any, Sequence


class VectorStoreProvider(Protocol):
    """Protocol for vector store providers."""

    def create_collection(
        self, collection_name: str, vector_size: int, **kwargs
    ) -> None:
        """
        Create a collection.

        Args:
            collection_name: Name of the collection
            vector_size: Dimension of vectors
            **kwargs: Provider-specific parameters
        """
        ...

    def upsert(
        self, collection_name: str, points: List[Dict[str, Any]], **kwargs
    ) -> None:
        """
        Upsert points into collection.

        Args:
            collection_name: Name of the collection
            points: List of points with id, vector, payload
            **kwargs: Provider-specific parameters
        """
        ...

    def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int = 10,
        **kwargs,
    ) -> List[Any]:
        """
        Search for similar vectors.

        Args:
            collection_name: Name of the collection
            query_vector: Query vector
            limit: Maximum number of results
            **kwargs: Provider-specific parameters

        Returns:
            List of search results
        """
        ...

    def delete(
        self, collection_name: str, point_ids: List[int | str], **kwargs
    ) -> None:
        """
        Delete points from collection.

        Args:
            collection_name: Name of the collection
            point_ids: List of point IDs to delete
            **kwargs: Provider-specific parameters
        """
        ...
