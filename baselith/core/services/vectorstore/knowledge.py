"""Text-level knowledge operations: ingest a document, search by text, delete.

The vector store speaks vectors and chunks. Every caller that works in text,
which is the HTTP knowledge API, the MCP tools and the workflow ``RETRIEVE``
node, needs the same few steps around it: load the embedder without blocking
the event loop, embed the query as a *query*, turn the hits into plain
records, and on the way in pick a chunking mode, replace the previous version
of a document and index. They live here so those callers cannot drift apart.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from baselith.core.models.domain import Document
from baselith.core.observability.logging import get_logger
from baselith.core.services.vectorstore.chunking import chunk_text
from baselith.core.services.vectorstore.exceptions import VectorStoreError

logger = get_logger(__name__)

__all__ = [
    "CHUNKING_MODES",
    "DOCUMENT_ID_PATTERN",
    "IngestError",
    "IngestResult",
    "KnowledgeHit",
    "delete_document",
    "ingest_document",
    "resolve_embedder",
    "search_by_text",
]

#: ``default`` splits by size, ``auto`` follows the document's structure
#: (markdown headings, python units) and ``qa`` makes one chunk per
#: question/answer pair, embedding the question.
CHUNKING_MODES = ("default", "auto", "qa")

#: A document id is a path segment, a log field and a payload value.
DOCUMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")

#: Payload keys that are pipeline internals, not something to show a client.
_HIDDEN_PAYLOAD_KEYS = frozenset(
    {"text", "chunk_body", "original_text", "tenant_id", "ingestion_chunks"}
)


class IngestError(ValueError):
    """The document could not be ingested; the message says why."""


@dataclass(frozen=True)
class IngestResult:
    """What an ingestion wrote."""

    document_id: str
    collection: str | None
    chunks: int
    chunking: str


@dataclass(frozen=True)
class KnowledgeHit:
    """One search hit, shaped for a client."""

    document_id: str
    chunk_index: int | None
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form."""
        return {
            "document_id": self.document_id,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "score": self.score,
            "metadata": self.metadata,
        }


async def resolve_embedder() -> Any:
    """The default embedder, built off the event loop.

    Constructing it loads a sentence-transformer model: slow, synchronous, and
    exactly what must not run on the loop that serves requests.
    """
    from baselith.core.nlp.models import get_embedder

    return await asyncio.to_thread(get_embedder)


def _service() -> Any:
    from baselith.core.services.vectorstore.service import get_vectorstore_service

    return get_vectorstore_service()


async def search_by_text(
    query: str,
    *,
    top_k: int = 5,
    collection: str | None = None,
    rerank: bool = False,
    min_score: float | None = None,
    embedder: Any | None = None,
    service: Any | None = None,
) -> list[KnowledgeHit]:
    """Semantic search for a text query, tenant-isolated by the store.

    Args:
        query: Natural-language query.
        top_k: Hits to return.
        collection: Collection; the configured default when ``None``.
        rerank: Re-score the candidates with the cross-encoder.
        min_score: Drop hits scoring below this.
        embedder: Embedder override (tests, custom models).
        service: Vector store service override.

    Returns:
        Hits, best first. An empty list means nothing relevant was found.

    Raises:
        VectorStoreError: The search itself failed. It is never reported as an
            empty result, because "nothing found" and "the store is down" are
            different answers.
    """
    from baselith.core.nlp.roles import aencode_query

    embedder = embedder or await resolve_embedder()
    encoded = await aencode_query(embedder, [query])
    store = service or _service()
    results = await store.search(
        query_vector=[float(v) for v in encoded[0]],
        k=top_k,
        collection_name=collection,
        query_text=query,
        rerank=rerank,
    )
    hits: list[KnowledgeHit] = []
    for result in results:
        if min_score is not None and result.score < min_score:
            continue
        payload = dict(result.document.metadata or {})
        index = payload.get("chunk_index")
        hits.append(
            KnowledgeHit(
                document_id=str(payload.get("document_id") or result.document.id),
                chunk_index=index if isinstance(index, int) else None,
                content=result.document.content,
                score=float(result.score),
                metadata={
                    k: v for k, v in payload.items() if k not in _HIDDEN_PAYLOAD_KEYS
                },
            )
        )
    return hits


def _chunks_for(
    content: str, chunking: str, source_name: str | None
) -> tuple[list[dict[str, Any]], int]:
    """``(precomputed ingestion chunks, number of chunks)`` for a mode.

    Precomputed chunks are empty for the default mode, which the indexing
    pipeline splits itself.
    """
    if chunking == "qa":
        from baselith.core.services.vectorstore.qa_splitter import qa_ingestion_chunks

        pairs = qa_ingestion_chunks(content)
        if not pairs:
            raise IngestError(
                "no question/answer pairs found: use 'Q:'/'A:' lines, a "
                "question,answer table, or markdown headings ending in '?'"
            )
        return pairs, len(pairs)
    if chunking == "auto":
        from baselith.core.services.vectorstore.splitters import (
            RecursiveTextSplitter,
            select_splitter,
        )

        splitter = select_splitter(source_name)
        if not isinstance(splitter, RecursiveTextSplitter):
            parts = [
                {
                    "text": chunk.text,
                    "chunk_body": chunk.text,
                    **(
                        {"headings": chunk.metadata["headings"]}
                        if chunk.metadata.get("headings")
                        else {}
                    ),
                    **{k: v for k, v in chunk.metadata.items() if k != "headings"},
                }
                for chunk in splitter.split(content)
            ]
            if parts:
                return parts, len(parts)
    return [], len(chunk_text(content))


async def _previous_chunk_count(
    store: Any, document_id: str, collection: str | None
) -> int:
    """How many chunks the stored version of a document has (0 if none/unknown)."""
    from baselith.core.context import get_current_tenant_id
    from baselith.core.services.vectorstore.chunking import chunk_point_id

    try:
        points = await store.retrieve(
            [chunk_point_id(document_id, 0, get_current_tenant_id())],
            collection_name=collection,
        )
    except VectorStoreError as exc:
        logger.warning(
            "knowledge_previous_version_unreadable document=%s error=%s; "
            "chunks of an older, longer version may remain",
            document_id,
            exc,
        )
        return 0
    for point in points or []:
        payload = getattr(point, "payload", None) or {}
        count = payload.get("chunk_count")
        if payload.get("document_id") == document_id and isinstance(count, int):
            return count
    return 0


async def _trim_stale_chunks(
    store: Any, document_id: str, collection: str | None, keep: int, previous: int
) -> None:
    """Delete chunks ``keep..previous-1`` left over from a longer older version."""
    from baselith.core.context import get_current_tenant_id
    from baselith.core.services.vectorstore.chunking import chunk_point_id

    if previous <= keep:
        return
    tenant = get_current_tenant_id()
    await store.provider.delete(
        collection_name=collection or store.config.collection_name,
        point_ids=[
            chunk_point_id(document_id, i, tenant) for i in range(keep, previous)
        ],
    )


async def ingest_document(
    content: str,
    *,
    document_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    collection: str | None = None,
    chunking: str = "default",
    source_name: str | None = None,
    replace: bool = True,
    embedder: Any | None = None,
    service: Any | None = None,
) -> IngestResult:
    """Chunk, embed and index one document.

    Args:
        content: The document text.
        document_id: Stable id; a random one when omitted. Ingesting the same
            id again replaces the earlier version (see ``replace``).
        metadata: Extra payload stored with every chunk.
        collection: Target collection; the configured default when ``None``.
        chunking: One of :data:`CHUNKING_MODES`.
        source_name: File name, used by ``auto`` to pick a structure-aware
            splitter.
        replace: Remove what an older, longer version left behind. Chunk ids
            are deterministic per tenant, document and position, so the new
            chunks overwrite the old ones in place; only the tail past the new
            length needs deleting. It is deleted *after* the new version is
            written, so a failure in between leaves the old document readable
            instead of absent.
        embedder: Embedder override.
        service: Vector store service override.

    Raises:
        IngestError: Bad id, unknown mode, empty content, a ``qa`` document
            with no pairs, or nothing was written.
    """
    if chunking not in CHUNKING_MODES:
        raise IngestError(f"unknown chunking mode {chunking!r}; use {CHUNKING_MODES}")
    if not content or not content.strip():
        raise IngestError("the document is empty")
    doc_id = document_id if document_id is not None else uuid.uuid4().hex
    if not DOCUMENT_ID_PATTERN.match(doc_id):
        raise IngestError(
            "invalid document id: 1-200 characters of letters, digits, '.', '_', "
            "':' or '-', starting with a letter or digit"
        )

    precomputed, planned = _chunks_for(content, chunking, source_name)
    payload_meta = dict(metadata or {})
    if precomputed:
        payload_meta["ingestion_chunks"] = precomputed
    document = Document(id=doc_id, content=content, metadata=payload_meta)

    store = service or _service()
    previous = await _previous_chunk_count(store, doc_id, collection) if replace else 0
    written = await store.index(
        [document],
        collection_name=collection,
        embedder=embedder or await resolve_embedder(),
    )
    if not written:
        raise IngestError("the store accepted the request but wrote nothing")
    await _trim_stale_chunks(store, doc_id, collection, planned, previous)
    return IngestResult(
        document_id=doc_id, collection=collection, chunks=planned, chunking=chunking
    )


async def delete_document(
    document_id: str, *, collection: str | None = None, service: Any | None = None
) -> None:
    """Remove every chunk of one document (tenant-isolated by the store).

    Raises:
        IngestError: The id is not a valid document id.
        VectorStoreError: The store failed.
    """
    if not DOCUMENT_ID_PATTERN.match(document_id):
        raise IngestError("invalid document id")
    await (service or _service()).delete_document(
        document_id, collection_name=collection
    )
