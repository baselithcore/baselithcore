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


class EmbeddingDimensionMismatchError(VectorStoreError):
    """The existing collection stores vectors of a different width.

    Raised at collection setup when ``VECTORSTORE_EMBEDDING_DIM`` disagrees
    with the live collection (Qdrant) or table column (pgvector).
    """
