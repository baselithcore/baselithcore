"""
Text chunking utilities for vector store.
"""

from functools import lru_cache
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 200
_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

# Default text splitter (reused for the common case to avoid rebuilding it
# on every call).
DEFAULT_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=DEFAULT_CHUNK_SIZE,
    chunk_overlap=DEFAULT_CHUNK_OVERLAP,
    length_function=len,
    separators=_SEPARATORS,
)


@lru_cache(maxsize=32)
def _get_splitter(
    chunk_size: int, chunk_overlap: int
) -> RecursiveCharacterTextSplitter:
    """Return a cached splitter for the given (chunk_size, chunk_overlap)."""
    if chunk_size == DEFAULT_CHUNK_SIZE and chunk_overlap == DEFAULT_CHUNK_OVERLAP:
        return DEFAULT_SPLITTER
    return RecursiveCharacterTextSplitter(
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


def chunk_point_id(document_id: str, chunk_index: int) -> int:
    """
    Generate a unique point ID for a chunk.

    Args:
        document_id: Document identifier
        chunk_index: Chunk index

    Returns:
        Unique point ID as integer
    """
    # Use hash to create numeric ID for Qdrant
    import hashlib

    combined = f"{document_id}::{chunk_index}"
    hash_obj = hashlib.sha256(combined.encode())
    # Convert first 8 bytes to int
    return int.from_bytes(hash_obj.digest()[:8], byteorder="big")
