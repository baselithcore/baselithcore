"""
VectorStore exceptions.
"""


class VectorStoreError(Exception):
    """Base exception for vector store errors."""

    pass


class CollectionNotFoundError(VectorStoreError):
    """Raised when a collection is not found."""

    pass


class IndexingError(VectorStoreError):
    """Raised when there's an error during indexing."""

    pass
