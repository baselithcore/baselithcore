"""Batched indexing flush + bounded concurrency helper.

Kept import-light (no document-source / spaCy imports) so it exercises the
batching logic directly.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from core.services.indexing._batch import build_document, flush_index_batch
from core.services.indexing.state import IndexedDocument, IndexingStats
from core.utils.concurrency import bounded_gather


class _RecordingVectorstore:
    def __init__(
        self, fail_batches: bool = False, silent_batches: bool = False
    ) -> None:
        self.calls: list[int] = []  # number of docs per index() call
        self.fail_batches = fail_batches
        # Mimics the real service, which swallows embedding/upsert errors and
        # signals them by returning 0 instead of raising.
        self.silent_batches = silent_batches

    async def index(self, *, documents, collection_name=None, embedder=None) -> int:
        self.calls.append(len(documents))
        if len(documents) > 1:
            if self.fail_batches:
                raise RuntimeError("batch upsert failed")
            if self.silent_batches:
                return 0
        return len(documents)


class _SilentVectorstore:
    """A store that reports zero written documents without ever raising."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def index(self, *, documents, collection_name=None, embedder=None) -> int:
        self.calls.append(len(documents))
        return 0


class _CountlessVectorstore:
    """A store that returns no count at all (legacy / out-of-tree stub)."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def index(self, *, documents, collection_name=None, embedder=None) -> None:
        self.calls.append(len(documents))
        return None


def _item(uid: str) -> Any:
    return SimpleNamespace(uid=uid, content="c", fingerprint=f"fp-{uid}", metadata={})


@pytest.mark.asyncio
async def test_flush_indexes_batch_in_one_call():
    vs = _RecordingVectorstore()
    stats = IndexingStats()
    indexed: dict[str, IndexedDocument] = {}
    batch = [(_item(f"d{i}"), build_document(_item(f"d{i}"))) for i in range(5)]

    await flush_index_batch(
        vectorstore=vs,
        embedder=None,
        collection_name="c",
        batch=batch,  # type: ignore[arg-type]
        indexed_items=indexed,
        stats=stats,
    )

    # One index() call carrying all five documents.
    assert vs.calls == [5]
    assert stats.new_documents == 5
    assert len(indexed) == 5


@pytest.mark.asyncio
async def test_flush_falls_back_per_document_on_batch_failure():
    vs = _RecordingVectorstore(fail_batches=True)
    stats = IndexingStats()
    indexed: dict[str, IndexedDocument] = {}
    batch = [(it := _item(f"d{i}"), build_document(it)) for i in range(3)]

    await flush_index_batch(
        vectorstore=vs,
        embedder=None,
        collection_name="c",
        batch=batch,  # type: ignore[arg-type]
        indexed_items=indexed,
        stats=stats,
    )

    # First a failed batch of 3, then three single-document retries that succeed.
    assert vs.calls == [3, 1, 1, 1]
    assert stats.new_documents == 3
    assert len(indexed) == 3


@pytest.mark.asyncio
async def test_flush_falls_back_when_batch_reports_short_count():
    """A silently short write must trigger the same isolation as an exception.

    ``VectorStoreService.index`` reports embedding/upsert failures by
    returning a reduced count, not by raising.
    """
    vs = _RecordingVectorstore(silent_batches=True)
    stats = IndexingStats()
    indexed: dict[str, IndexedDocument] = {}
    batch = [(it := _item(f"d{i}"), build_document(it)) for i in range(3)]

    await flush_index_batch(
        vectorstore=vs,
        embedder=None,
        collection_name="c",
        batch=batch,  # type: ignore[arg-type]
        indexed_items=indexed,
        stats=stats,
    )

    assert vs.calls == [3, 1, 1, 1]
    assert stats.new_documents == 3
    assert len(indexed) == 3


@pytest.mark.asyncio
async def test_flush_does_not_record_documents_the_store_never_wrote():
    """Never fingerprint a document the store did not accept.

    A recorded fingerprint makes every later incremental run skip the
    document, so an optimistic record on a failed write loses it forever.
    """
    vs = _SilentVectorstore()
    stats = IndexingStats()
    indexed: dict[str, IndexedDocument] = {}
    batch = [(it := _item(f"d{i}"), build_document(it)) for i in range(3)]

    await flush_index_batch(
        vectorstore=vs,
        embedder=None,
        collection_name="c",
        batch=batch,  # type: ignore[arg-type]
        indexed_items=indexed,
        stats=stats,
    )

    # Batch, then a per-document retry each — all of which also write nothing.
    assert vs.calls == [3, 1, 1, 1]
    assert stats.new_documents == 0
    assert indexed == {}


@pytest.mark.asyncio
async def test_flush_stays_optimistic_when_store_reports_no_count():
    """A store that returns ``None`` keeps the historical behaviour."""
    vs = _CountlessVectorstore()
    stats = IndexingStats()
    indexed: dict[str, IndexedDocument] = {}
    batch = [(it := _item(f"d{i}"), build_document(it)) for i in range(2)]

    await flush_index_batch(
        vectorstore=vs,
        embedder=None,
        collection_name="c",
        batch=batch,  # type: ignore[arg-type]
        indexed_items=indexed,
        stats=stats,
    )

    assert vs.calls == [2]
    assert stats.new_documents == 2
    assert len(indexed) == 2


@pytest.mark.asyncio
async def test_bounded_gather_caps_concurrency():
    active = 0
    peak = 0

    async def worker() -> int:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return 1

    results = await bounded_gather((worker() for _ in range(20)), limit=4)
    assert sum(results) == 20  # type: ignore[arg-type]
    assert peak <= 4


@pytest.mark.asyncio
async def test_bounded_gather_return_exceptions():
    async def ok() -> int:
        return 1

    async def boom() -> int:
        raise ValueError("x")

    results = await bounded_gather(
        [ok(), boom(), ok()], limit=2, return_exceptions=True
    )
    assert results[0] == 1
    assert isinstance(results[1], ValueError)
    assert results[2] == 1
