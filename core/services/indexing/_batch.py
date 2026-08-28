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


def _short_of(written: Any, expected: int) -> bool:
    """True when the store reported writing fewer documents than we sent.

    ``VectorStoreService.index`` swallows embedding and upsert errors and
    reports the shortfall through its return value instead of raising, so the
    count — not just the absence of an exception — decides whether a batch
    really landed. A non-``int`` result means the injected store does not
    report counts (test doubles, out-of-tree providers); there is nothing to
    compare, so we keep the historical optimistic reading.
    """
    return (
        isinstance(written, int)
        and not isinstance(written, bool)
        and written < expected
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

    A batch counts as failed both when ``index()`` raises **and** when it
    reports fewer written documents than were handed to it — the vector store
    turns embedding/upsert errors into a reduced count rather than an
    exception. Recording an item as indexed writes its fingerprint into the
    incremental-run registry, so a document marked on a failed write would be
    skipped by every later run and never reach the store.

    The isolation this provides is only as strong as the store's error
    reporting: it covers everything the upsert surfaces synchronously. That is
    why the indexing path upserts with ``wait=True`` (see
    ``core.services.vectorstore._indexing``) — a fire-and-forget write would
    acknowledge before the failure is known and defeat the fallback.
    """
    if not batch:
        return
    docs = [doc for _, doc in batch]
    reason: str | None = None
    try:
        written = await vectorstore.index(
            documents=docs,
            collection_name=collection_name,
            embedder=embedder,
        )
    except Exception as exc:
        reason = str(exc)
    else:
        if _short_of(written, len(docs)):
            reason = f"only {written}/{len(docs)} documents written"

    if reason is None:
        for item, _ in batch:
            _record_indexed(item, indexed_items)
            stats.new_documents += 1
        return

    logger.warning(
        f"[indexing] Batch of {len(docs)} failed ({reason}); retrying per-document"
    )
    for item, doc in batch:
        try:
            written_one = await vectorstore.index(
                documents=[doc],
                collection_name=collection_name,
                embedder=embedder,
            )
        except Exception as inner:
            logger.error(f"[indexing] Failed to index {item.uid}: {inner}")
            continue
        if _short_of(written_one, 1):
            logger.error(
                f"[indexing] Failed to index {item.uid}: vector store wrote nothing"
            )
            continue
        _record_indexed(item, indexed_items)
        stats.new_documents += 1


__all__ = ["build_document", "flush_index_batch"]
