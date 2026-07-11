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

        chunks = chunk_text(content)
        if not chunks:
            logger.warning(f"No chunks generated for document {doc_id}")
            continue

        enriched_chunks = [prepare_chunk_text(chunk, metadata) for chunk in chunks]

        doc_plans.append(
            {
                "doc": doc,
                "doc_id": doc_id,
                "metadata": metadata,
                "chunks": chunks,
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
        offset = plan["offset"]
        doc_vectors = all_vectors[offset : offset + len(chunks)]

        doc_points = []
        for idx, (chunk, vector) in enumerate(zip(chunks, doc_vectors)):
            payload = {
                "text": chunk,
                "source": getattr(doc, "clean_path", doc.id),
                "document_id": doc_id,
                "tenant_id": current_tenant,
                "chunk_index": idx,
                "chunk_count": len(chunks),
            }
            payload.update(metadata)

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

    # 4. Single bulk upsert for the whole batch. wait=False lets Qdrant
    #    acknowledge without blocking on the index flush (callers may
    #    override via kwargs).
    upsert_kwargs = {"wait": False, **kwargs}
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
