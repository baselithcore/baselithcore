"""
Text chunking utilities for vector store.
"""

from functools import lru_cache
from typing import Any

from baselith.core.observability.logging import get_logger
from baselith.core.services.vectorstore.recursive_splitter import (
    RecursiveCharacterSplitter,
)

logger = get_logger(__name__)

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 200
_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

# Default text splitter (reused for the common case to avoid rebuilding it
# on every call).
DEFAULT_SPLITTER = RecursiveCharacterSplitter(
    chunk_size=DEFAULT_CHUNK_SIZE,
    chunk_overlap=DEFAULT_CHUNK_OVERLAP,
    length_function=len,
    separators=_SEPARATORS,
)


@lru_cache(maxsize=32)
def _get_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterSplitter:
    """Return a cached splitter for the given (chunk_size, chunk_overlap)."""
    if chunk_size == DEFAULT_CHUNK_SIZE and chunk_overlap == DEFAULT_CHUNK_OVERLAP:
        return DEFAULT_SPLITTER
    return RecursiveCharacterSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=_SEPARATORS,
    )


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """
    Split text into chunks.

    Args:
        text: Text to split
        chunk_size: Maximum chunk size
        chunk_overlap: Overlap between chunks

    Returns:
        List of text chunks
    """
    if not text or not text.strip():
        return []

    splitter = _get_splitter(chunk_size, chunk_overlap)

    chunks = [chunk for chunk in splitter.split_text(text) if chunk.strip()]
    logger.debug(f"Split text into {len(chunks)} chunks")
    return chunks


def prepare_chunk_text(chunk: str, metadata: dict[str, Any] | None = None) -> str:
    """
    Prepare chunk text with optional metadata enrichment.

    Args:
        chunk: Raw chunk text
        metadata: Optional metadata to prepend

    Returns:
        Enriched chunk text
    """
    if not metadata:
        return chunk

    # Add filename/source context if available
    prefix_parts = []

    if "filename" in metadata:
        prefix_parts.append(f"File: {metadata['filename']}")
    elif "source" in metadata:
        prefix_parts.append(f"Source: {metadata['source']}")

    if prefix_parts:
        return "\n".join(prefix_parts) + "\n\n" + chunk

    return chunk


def chunk_point_id(
    document_id: str, chunk_index: int, tenant_id: str | None = None
) -> int:
    """
    Generate a unique point ID for a chunk.

    The id is the point's primary key, and an upsert on an existing id replaces
    the stored payload, tenant included. An id derived from the document id
    alone therefore let two tenants that used the same document id (a file
    name, an id a client picked) write over each other's chunks, and the later
    writer then owned them. A non-default tenant is mixed into the hash so ids
    cannot collide across tenants.

    The default tenant keeps the original derivation, byte for byte: a
    single-tenant deployment re-indexing a document must land on the points it
    already has instead of creating a second copy.

    Args:
        document_id: Document identifier
        chunk_index: Chunk index
        tenant_id: Tenant owning the document; ``None`` or ``"default"`` for the
            single-tenant case

    Returns:
        Unique point ID as integer
    """
    # Use hash to create numeric ID for Qdrant
    import hashlib

    if tenant_id in (None, "", "default"):
        combined = f"{document_id}::{chunk_index}"
    else:
        combined = f"{tenant_id}\x1f{document_id}::{chunk_index}"
    hash_obj = hashlib.sha256(combined.encode())
    # Convert first 8 bytes to int
    return int.from_bytes(hash_obj.digest()[:8], byteorder="big")
