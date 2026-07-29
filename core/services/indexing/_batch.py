"""Document building + batched flush for the indexing service.

Extracted from ``service.py`` (module size cap). ``flush_index_batch`` indexes
many documents in a single ``vectorstore.index`` call — one embedding pass and
one bulk upsert per batch instead of one round-trip per document — and falls
back to per-document indexing if the batch fails, so a single poison document
cannot drop its whole batch.
"""

from __future__ import annotations

from typing import Any

from core.models.domain import Document
from core.observability.logging import get_logger

from .state import IndexedDocument, IndexingStats

logger = get_logger(__name__)


def build_document(item: Any) -> Document | None:
    """Turn a raw source item into a domain :class:`Document` (None if empty)."""
    content = item.content
    if not content:
        return None
    doc = Document(id=item.uid, content=content, metadata=item.metadata or {})
    if "source" not in doc.metadata:
        doc.metadata["source"] = getattr(item, "clean_path", item.uid)
    return doc


def _record_indexed(item: Any, indexed_items: dict[str, IndexedDocument]) -> None:
    indexed_items[item.uid] = IndexedDocument(
        fingerprint=item.fingerprint,
        metadata=dict(item.metadata or {}),
    )


async def flush_index_batch(
    *,
    vectorstore: Any,
    embedder: Any,
    collection_name: str,
    batch: list[tuple[Any, Document]],
    indexed_items: dict[str, IndexedDocument],
    stats: IndexingStats,
) -> None:
    """Index a batch of ``(item, Document)`` pairs in one call.

    On success records each item as indexed and bumps ``stats.new_documents``.
    On batch failure, retries each document on its own so the good ones still
    land and only the failing document is dropped (with a log line).
    """
    if not batch:
        return
    docs = [doc for _, doc in batch]
    try:
        await vectorstore.index(
            documents=docs,
            collection_name=collection_name,
            embedder=embedder,
        )
    except Exception as exc:
        logger.warning(
            f"[indexing] Batch of {len(docs)} failed ({exc}); retrying per-document"
        )
        for item, doc in batch:
            try:
                await vectorstore.index(
                    documents=[doc],
                    collection_name=collection_name,
                    embedder=embedder,
                )
            except Exception as inner:
                logger.error(f"[indexing] Failed to index {item.uid}: {inner}")
                continue
            _record_indexed(item, indexed_items)
            stats.new_documents += 1
        return
    for item, _ in batch:
        _record_indexed(item, indexed_items)
        stats.new_documents += 1


__all__ = ["build_document", "flush_index_batch"]
