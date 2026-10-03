"""Existing-collection width vs ``VECTORSTORE_EMBEDDING_DIM``.

Collection setup is idempotent — it leaves an existing collection (Qdrant) or
table (pgvector) alone. Without a check, changing the embedding model or the
configured dimension was silent at setup and surfaced later as per-batch
upsert failures, or as searches whose scores meant nothing. Both providers
compare the live width with the configured one when they set the collection
up and raise :class:`EmbeddingDimensionMismatchError` — never dropping data.

Deliberately free of ``qdrant_client`` imports (an optional extra): the
Qdrant collection info is read by shape.
"""

from __future__ import annotations

from typing import Any

from core.services.vectorstore.exceptions import EmbeddingDimensionMismatchError


def dimension_mismatch_error(
    backend: str, collection: str, existing: int, configured: int
) -> EmbeddingDimensionMismatchError:
    """Build the actionable error for a width mismatch.

    Args:
        backend: ``"qdrant"`` or ``"pgvector"``, for the message.
        collection: The collection (or table) name.
        existing: Width stored by the live collection.
        configured: Width the configuration asks for.
    """
    return EmbeddingDimensionMismatchError(
        f"{backend} collection {collection!r} stores {existing}-dimensional "
        f"vectors but VECTORSTORE_EMBEDDING_DIM is {configured}. The collection "
        "was left untouched. Either set VECTORSTORE_EMBEDDING_DIM="
        f"{existing} (and the embedding model that produced it), or point "
        "VECTORSTORE_COLLECTION_NAME at a new collection and re-index; drop the "
        "old one by hand once it is no longer needed."
    )


def qdrant_vector_size(collection_info: Any) -> int | None:
    """Width of a Qdrant collection's dense vector, or ``None`` when unknown.

    Handles the unnamed form (``VectorParams``) and a single named vector
    (``{"name": VectorParams}``). Several named vectors are ambiguous here and
    yield ``None`` (no check rather than a wrong one).
    """
    config = getattr(collection_info, "config", None)
    params = getattr(config, "params", None)
    vectors = getattr(params, "vectors", None)
    if isinstance(vectors, dict):
        if len(vectors) != 1:
            return None
        vectors = next(iter(vectors.values()))
    size = getattr(vectors, "size", None)
    return size if isinstance(size, int) else None


async def assert_qdrant_dimension(client: Any, collection: str, size: int) -> None:
    """Raise when an existing Qdrant collection's width differs from ``size``.

    Args:
        client: An ``AsyncQdrantClient`` (or a compatible double).
        collection: The existing collection to inspect.
        size: The configured embedding dimension.

    Raises:
        EmbeddingDimensionMismatchError: The widths differ.
    """
    existing = qdrant_vector_size(await client.get_collection(collection))
    if existing is not None and existing != int(size):
        raise dimension_mismatch_error("qdrant", collection, existing, int(size))


__all__ = [
    "EmbeddingDimensionMismatchError",
    "assert_qdrant_dimension",
    "dimension_mismatch_error",
    "qdrant_vector_size",
]
