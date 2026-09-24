"""Batch indexing pipeline for the VectorStore service.

Body of ``VectorStoreService.index`` — chunk → single embedding pass →
tenant-scoped point assembly → single bulk upsert. Extracted (module size
cap) so ``service.py`` keeps only the thin public surface; behavior is
unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from core.context import get_current_tenant_id
from core.models.domain import Document
from core.observability.logging import get_logger
from core.services.vectorstore.chunking import (
    chunk_point_id,
    chunk_text,
    prepare_chunk_text,
)
from core.services.vectorstore.embedding_cache import (
    EmbedderProtocol,
    get_embeddings_cached,
)
from core.services.vectorstore.exceptions import VectorStoreError

if TYPE_CHECKING:
    from core.services.vectorstore.service import VectorStoreService

logger = get_logger(__name__)

#: Payload keys the pipeline owns. Retrieval reads all of them and tenant
#: isolation reads ``tenant_id``, so caller metadata may not set any of them.
RESERVED_PAYLOAD_KEYS = frozenset(
    {
        "text",
        "chunk_body",
        "source",
        "document_id",
        "tenant_id",
        "chunk_index",
        "chunk_count",
        "ingestion_chunks",
    }
)


def _normalise_precomputed_chunks(
    doc: Document, metadata: dict[str, Any]
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Return ``(payload_texts, embedding_texts, per_chunk_payloads)``.

    Document readers may attach structured chunks under ``metadata["ingestion_chunks"]``.
    When absent or malformed we preserve the historical 800/200 character
    splitter path.
    """
    raw_chunks = metadata.get("ingestion_chunks")
    if not isinstance(raw_chunks, list) or not raw_chunks:
        chunks = chunk_text(doc.content)
        return (
            chunks,
            [prepare_chunk_text(chunk, metadata) for chunk in chunks],
            [{} for _ in chunks],
        )

    payload_texts: list[str] = []
    embedding_texts: list[str] = []
    chunk_payloads: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_chunks):
        if not isinstance(raw, dict):
            continue
        payload_text = str(
            raw.get("text") or raw.get("chunk_body") or raw.get("context_text") or ""
        ).strip()
        if not payload_text:
            continue
        embedding_text = str(raw.get("embedding_text") or payload_text).strip()
        payload_texts.append(payload_text)
        embedding_texts.append(prepare_chunk_text(embedding_text, metadata))
        chunk_payloads.append(
            {
                "chunk_body": str(
                    raw.get("chunk_body") or raw.get("context_text") or payload_text
                ),
                "original_text": raw.get("original_text")
                or raw.get("text")
                or payload_text,
                "pages": raw.get("pages") or [],
                "headings": raw.get("headings") or [],
                "provenance": raw.get("provenance") or [],
                "parser": raw.get("parser"),
                "source_index": raw.get("source_index", index),
            }
        )

    if not payload_texts:
        chunks = chunk_text(doc.content)
        return (
            chunks,
            [prepare_chunk_text(chunk, metadata) for chunk in chunks],
            [{} for _ in chunks],
        )

    return payload_texts, embedding_texts, chunk_payloads


async def index_documents(
    service: VectorStoreService,
    documents: Sequence[Document],
    collection_name: str | None = None,
    embedder: EmbedderProtocol | None = None,
    **kwargs: Any,
) -> int:
    """Index a batch of documents. See ``VectorStoreService.index``."""
    collection_name = collection_name or service.config.collection_name

    if not embedder:
        raise VectorStoreError("Embedder is required for indexing documents")

    # 1. Chunk every document up front, tracking which chunks belong to
    #    which document so we can re-assemble points after a single embed.
    all_enriched_chunks: list[str] = []
    doc_plans: list[dict[str, Any]] = []

    for doc in documents:
        doc_id = doc.id
        content = doc.content
        metadata = doc.metadata

        if not doc_id or not content:
            logger.warning("Skipping document with missing id or content")
            continue

        chunks, enriched_chunks, chunk_payloads = _normalise_precomputed_chunks(
            doc, metadata
        )
        if not chunks:
            logger.warning(f"No chunks generated for document {doc_id}")
            continue

        doc_plans.append(
            {
                "doc": doc,
                "doc_id": doc_id,
                "metadata": metadata,
                "chunks": chunks,
                "chunk_payloads": chunk_payloads,
                "offset": len(all_enriched_chunks),
            }
        )
        all_enriched_chunks.extend(enriched_chunks)

    if not all_enriched_chunks:
        logger.info(f"Indexing complete: 0/{len(documents)} documents processed")
        return 0

    # 2. Single embedding pass over every chunk in the batch.
    try:
        all_vectors = await get_embeddings_cached(
            embedder,
            all_enriched_chunks,
            service.cache,
            model_id=service.config.embedding_model,
        )
    except Exception as e:
        logger.error(f"Failed to generate embeddings for batch: {e}")
        return 0

    # 3. Point creation: rebuild per-document points from the shared vectors.
    points: list[dict[str, Any]] = []
    current_tenant = get_current_tenant_id()
    indexed_count = 0

    for plan in doc_plans:
        doc = plan["doc"]
        doc_id = plan["doc_id"]
        metadata = plan["metadata"]
        chunks = plan["chunks"]
        chunk_payloads = plan["chunk_payloads"]
        offset = plan["offset"]
        doc_vectors = all_vectors[offset : offset + len(chunks)]

        # Caller metadata cannot shadow a key the pipeline owns. The merge
        # used to run the other way (``payload.update(metadata)`` after the
        # literal), so a document whose metadata carried ``tenant_id`` named
        # whatever tenant it liked — and every isolation check downstream reads
        # back this same payload (the pgvector ``payload @>`` predicate, the
        # Qdrant field condition), so one poisoned write was readable by the
        # tenant it named. Computed per document: the shadowing set cannot vary
        # between chunks of the same document.
        shadowed = RESERVED_PAYLOAD_KEYS.intersection(metadata)
        if shadowed:
            logger.warning(
                "indexing_metadata_reserved_keys_dropped",
                document_id=doc_id,
                keys=sorted(shadowed),
            )
        safe_metadata = {k: v for k, v in metadata.items() if k not in shadowed}

        doc_points = []
        for idx, (chunk, vector, chunk_payload) in enumerate(
            zip(chunks, doc_vectors, chunk_payloads, strict=True)
        ):
            payload = {
                **safe_metadata,
                **{k: v for k, v in chunk_payload.items() if v not in (None, [], {})},
                "text": chunk,
                "source": getattr(doc, "clean_path", doc.id),
                "document_id": doc_id,
                "tenant_id": current_tenant,
                "chunk_index": idx,
                "chunk_count": len(chunks),
            }

            doc_points.append(
                {
                    "id": chunk_point_id(doc_id, idx),
                    "vector": vector,
                    "payload": payload,
                }
            )

        if doc_points:
            points.extend(doc_points)
            indexed_count += 1

    if not points:
        logger.info(f"Indexing complete: 0/{len(documents)} documents processed")
        return 0

    # 4. Single bulk upsert for the whole batch.
    #
    #    ``wait=True`` is deliberate and must stay the default. A fire-and-
    #    forget upsert (``wait=False``) buys very little here and costs two
    #    guarantees this service is expected to provide:
    #
    #    * Read-after-write. ``index()`` is not only the bulk-ingestion path:
    #      ``VectorMemoryProvider.add()`` funnels every single-item memory
    #      write through it, and the agent loop may ``recall()`` that memory
    #      in a later step of the same turn. With ``wait=False`` Qdrant
    #      answers ``acknowledged`` before the point is searchable, so the
    #      write can silently go missing from the very next query.
    #    * Failure visibility. ``wait=False`` reports only request-level
    #      rejections; anything failing after acceptance is never surfaced to
    #      the caller. Qdrant exposes no flush/fsync primitive, so there is no
    #      cheap end-of-run barrier that could recover it: an extra
    #      ``wait=True`` operation only proves ordering on the shard it lands
    #      on (points are hash-routed, so it does not cover a multi-shard
    #      collection) and never propagates an earlier operation's error.
    #
    #    The upside is correspondingly small: batches are flushed
    #    sequentially (``IndexingService._process_source``) and each one is
    #    dominated by its embedding pass, next to which the upsert ack is
    #    noise. Callers who knowingly accept a non-durable write can still
    #    opt in per call via ``wait=False`` in ``**kwargs``.
    upsert_kwargs = {"wait": True, **kwargs}
    try:
        await service.provider.upsert(
            collection_name=collection_name, points=points, **upsert_kwargs
        )
        logger.debug(
            f"Indexed {indexed_count} documents ({len(points)} chunks) in one batch"
        )
    except Exception as e:
        logger.error(f"Failed to upsert batch of {len(points)} points: {e}")
        return 0

    logger.info(
        f"Indexing complete: {indexed_count}/{len(documents)} documents processed"
    )
    return indexed_count


__all__ = ["index_documents"]
