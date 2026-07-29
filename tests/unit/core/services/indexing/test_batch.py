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
    def __init__(self, fail_batches: bool = False) -> None:
        self.calls: list[int] = []  # number of docs per index() call
        self.fail_batches = fail_batches

    async def index(self, *, documents, collection_name=None, embedder=None) -> int:
        self.calls.append(len(documents))
        if self.fail_batches and len(documents) > 1:
            raise RuntimeError("batch upsert failed")
        return len(documents)


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
