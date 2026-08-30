"""Hierarchical (parent/child) chunking for small-to-big retrieval.

Splits a document into large *parent* chunks and each parent into small
*child* chunks. Children are what gets embedded and indexed — each carries
a deterministic ``parent_id`` — while parent texts live in an injected
:class:`ParentStore`. At query time, :func:`expand_to_parents` maps child
hits back to their (deduplicated) parent texts so the LLM sees full
context while retrieval stays precise.

This layer is opt-in and composable: the default indexing pipeline in
``_indexing.py`` is untouched. Compose it explicitly, e.g.::

    store = InMemoryParentStore()
    chunker = HierarchicalChunker(store)
    children = await chunker.chunk(doc_id, text, metadata)
    # embed/index children (child.text + child.metadata), then later:
    parents = await expand_to_parents(hits, store)
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.observability.logging import get_logger
from core.services.vectorstore.chunking import chunk_text

logger = get_logger(__name__)

DEFAULT_PARENT_CHUNK_SIZE = 2000
DEFAULT_PARENT_CHUNK_OVERLAP = 0
DEFAULT_CHILD_CHUNK_SIZE = 400
DEFAULT_CHILD_CHUNK_OVERLAP = 50


def parent_chunk_id(document_id: str, parent_index: int) -> str:
    """Deterministic parent chunk id from document id and parent index.

    Args:
        document_id: Identifier of the source document.
        parent_index: Zero-based index of the parent chunk.

    Returns:
        A 32-character hex string, stable across runs.
    """
    combined = f"{document_id}::parent::{parent_index}"
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class StoredParent:
    """A parent chunk as returned by a :class:`ParentStore`."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ParentStore(Protocol):
    """Storage for parent chunk texts, keyed by ``parent_id``."""

    async def put(self, parent_id: str, text: str, metadata: dict[str, Any]) -> None:
        """Store one parent chunk."""
        ...

    async def get(self, parent_id: str) -> StoredParent | None:
        """Fetch one parent chunk, or ``None`` if unknown."""
        ...


class InMemoryParentStore:
    """Dict-backed :class:`ParentStore` for tests and single-process use."""

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._parents: dict[str, StoredParent] = {}

    async def put(self, parent_id: str, text: str, metadata: dict[str, Any]) -> None:
        """Store one parent chunk (metadata is copied)."""
        self._parents[parent_id] = StoredParent(text=text, metadata=dict(metadata))

    async def get(self, parent_id: str) -> StoredParent | None:
        """Fetch one parent chunk, or ``None`` if unknown."""
        return self._parents.get(parent_id)

    def __len__(self) -> int:
        """Number of stored parents."""
        return len(self._parents)


@dataclass(frozen=True)
class ChildChunk:
    """An indexable child chunk linked to its parent.

    Attributes:
        text: The child chunk content (what gets embedded).
        parent_id: Deterministic id of the parent chunk.
        parent_index: Zero-based index of the parent in the document.
        child_index: Zero-based index of the child within its parent.
        metadata: Document metadata merged with the linkage fields
            (``parent_id``, ``parent_index``, ``child_index``) — ready to
            be used as an index payload.
    """

    text: str
    parent_id: str
    parent_index: int
    child_index: int
    metadata: dict[str, Any] = field(default_factory=dict)


class HierarchicalChunker:
    """Split documents into parent chunks and indexable child chunks.

    Parents are persisted into the injected :class:`ParentStore`; the
    returned children are what the caller embeds and indexes.
    """

    def __init__(
        self,
        parent_store: ParentStore,
        *,
        parent_chunk_size: int = DEFAULT_PARENT_CHUNK_SIZE,
        parent_chunk_overlap: int = DEFAULT_PARENT_CHUNK_OVERLAP,
        child_chunk_size: int = DEFAULT_CHILD_CHUNK_SIZE,
        child_chunk_overlap: int = DEFAULT_CHILD_CHUNK_OVERLAP,
    ) -> None:
        """Initialize the chunker.

        Args:
            parent_store: Destination for parent chunk texts.
            parent_chunk_size: Target parent size in characters.
            parent_chunk_overlap: Overlap between parents (default 0 so a
                child belongs to exactly one parent).
            child_chunk_size: Target child size in characters.
            child_chunk_overlap: Overlap between sibling children.

        Raises:
            ValueError: If ``child_chunk_size`` is not smaller than
                ``parent_chunk_size``.
        """
        if child_chunk_size >= parent_chunk_size:
            raise ValueError(
                "child_chunk_size must be smaller than parent_chunk_size "
                f"({child_chunk_size} >= {parent_chunk_size})"
            )
        self._parent_store = parent_store
        self._parent_chunk_size = parent_chunk_size
        self._parent_chunk_overlap = parent_chunk_overlap
        self._child_chunk_size = child_chunk_size
        self._child_chunk_overlap = child_chunk_overlap

    async def chunk(
        self,
        document_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[ChildChunk]:
        """Chunk ``text`` hierarchically, persisting parents to the store.

        Args:
            document_id: Stable id of the document (seed for parent ids).
            text: Full document text.
            metadata: Optional document metadata, propagated to both
                parents and children.

        Returns:
            The child chunks to embed/index, in document order.
        """
        if not text or not text.strip():
            return []

        base_metadata = dict(metadata or {})
        parent_texts = chunk_text(
            text, self._parent_chunk_size, self._parent_chunk_overlap
        )

        children: list[ChildChunk] = []
        for parent_index, parent_text in enumerate(parent_texts):
            parent_id = parent_chunk_id(document_id, parent_index)
            await self._parent_store.put(
                parent_id,
                parent_text,
                {
                    **base_metadata,
                    "document_id": document_id,
                    "parent_index": parent_index,
                },
            )
            child_texts = chunk_text(
                parent_text, self._child_chunk_size, self._child_chunk_overlap
            )
            children.extend(
                ChildChunk(
                    text=child_text,
                    parent_id=parent_id,
                    parent_index=parent_index,
                    child_index=child_index,
                    metadata={
                        **base_metadata,
                        "parent_id": parent_id,
                        "parent_index": parent_index,
                        "child_index": child_index,
                    },
                )
                for child_index, child_text in enumerate(child_texts)
            )

        logger.debug(
            f"Hierarchical chunking of {document_id}: "
            f"{len(parent_texts)} parents, {len(children)} children"
        )
        return children


@dataclass(frozen=True)
class ExpandedParent:
    """A parent chunk resolved from child hits during retrieval."""

    parent_id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


def _hit_parent_id(hit: Any) -> str | None:
    """Extract ``parent_id`` from a heterogeneous retrieval hit."""
    if isinstance(hit, Mapping):
        value = hit.get("parent_id")
        if value is None:
            for key in ("payload", "metadata"):
                nested = hit.get(key)
                if isinstance(nested, Mapping) and nested.get("parent_id") is not None:
                    value = nested.get("parent_id")
                    break
        return str(value) if value is not None else None

    value = getattr(hit, "parent_id", None)
    if value is None:
        document = getattr(hit, "document", None)
        nested = (
            getattr(document, "metadata", None)
            if document is not None
            else getattr(hit, "payload", None)
        )
        if isinstance(nested, Mapping):
            value = nested.get("parent_id")
    return str(value) if value is not None else None


def _hit_score(hit: Any) -> float:
    """Extract a numeric score from a retrieval hit (0.0 if absent)."""
    raw = (
        hit.get("score", 0.0)
        if isinstance(hit, Mapping)
        else getattr(hit, "score", 0.0)
    )
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


async def expand_to_parents(
    hits: Sequence[Any],
    parent_store: ParentStore,
    *,
    dedupe: bool = True,
) -> list[ExpandedParent]:
    """Map child hits to their parent texts.

    Args:
        hits: Retrieval hits carrying a ``parent_id`` — mappings (top-level
            or nested under ``payload``/``metadata``) or objects such as
            ``SearchResult`` (via ``document.metadata``). Hits without a
            ``parent_id`` are skipped.
        parent_store: Store holding the parent chunks; unknown parents are
            skipped.
        dedupe: If True (default), each parent appears once, scored by its
            best child hit and ordered by that score (descending). If
            False, one entry per hit in the original order.

    Returns:
        The expanded parents with text, metadata and child-derived score.
    """
    resolved: list[tuple[str, float]] = []
    for hit in hits:
        parent_id = _hit_parent_id(hit)
        if parent_id is None:
            logger.debug("Skipping hit without parent_id during parent expansion")
            continue
        resolved.append((parent_id, _hit_score(hit)))

    if dedupe:
        best: dict[str, float] = {}
        for parent_id, score in resolved:
            if parent_id not in best or score > best[parent_id]:
                best[parent_id] = score
        ordered = sorted(best.items(), key=lambda item: item[1], reverse=True)
    else:
        ordered = resolved

    expanded: list[ExpandedParent] = []
    cache: dict[str, StoredParent | None] = {}
    for parent_id, score in ordered:
        if parent_id not in cache:
            cache[parent_id] = await parent_store.get(parent_id)
        parent = cache[parent_id]
        if parent is None:
            logger.debug(f"Parent {parent_id} not found in store; hit skipped")
            continue
        expanded.append(
            ExpandedParent(
                parent_id=parent_id,
                text=parent.text,
                score=score,
                metadata=dict(parent.metadata),
            )
        )
    return expanded


__all__ = [
    "DEFAULT_CHILD_CHUNK_OVERLAP",
    "DEFAULT_CHILD_CHUNK_SIZE",
    "DEFAULT_PARENT_CHUNK_OVERLAP",
    "DEFAULT_PARENT_CHUNK_SIZE",
    "ChildChunk",
    "ExpandedParent",
    "HierarchicalChunker",
    "InMemoryParentStore",
    "ParentStore",
    "StoredParent",
    "expand_to_parents",
    "parent_chunk_id",
]
