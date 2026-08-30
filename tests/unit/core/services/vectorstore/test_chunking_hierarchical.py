"""Unit tests for hierarchical (parent/child) chunking."""

from core.models.domain import Document, SearchResult
from core.services.vectorstore.chunking_hierarchical import (
    HierarchicalChunker,
    InMemoryParentStore,
    expand_to_parents,
    parent_chunk_id,
)


def _document(n_sentences: int = 200) -> str:
    return " ".join(
        f"Sentence number {i} with a few extra padding words here."
        for i in range(n_sentences)
    )


class TestParentChunkId:
    def test_deterministic(self):
        assert parent_chunk_id("doc-1", 0) == parent_chunk_id("doc-1", 0)

    def test_varies_with_index_and_document(self):
        ids = {
            parent_chunk_id("doc-1", 0),
            parent_chunk_id("doc-1", 1),
            parent_chunk_id("doc-2", 0),
        }
        assert len(ids) == 3

    def test_is_hex_string(self):
        pid = parent_chunk_id("doc-1", 0)
        assert isinstance(pid, str)
        int(pid, 16)  # raises if not hex


class TestInMemoryParentStore:
    async def test_roundtrip(self):
        store = InMemoryParentStore()
        await store.put("p1", "parent text", {"source": "s"})
        stored = await store.get("p1")
        assert stored is not None
        assert stored.text == "parent text"
        assert stored.metadata == {"source": "s"}

    async def test_get_missing_returns_none(self):
        store = InMemoryParentStore()
        assert await store.get("nope") is None


class TestHierarchicalChunker:
    async def test_child_sizes_and_parent_linkage(self):
        store = InMemoryParentStore()
        chunker = HierarchicalChunker(
            store,
            parent_chunk_size=500,
            parent_chunk_overlap=0,
            child_chunk_size=120,
            child_chunk_overlap=0,
        )
        children = await chunker.chunk("doc-1", _document(), {"source": "unit"})

        assert len(children) > 1
        assert all(len(c.text) <= 120 for c in children)
        # Every child links to a stored parent that contains its text.
        for child in children:
            assert child.parent_id == parent_chunk_id("doc-1", child.parent_index)
            parent = await store.get(child.parent_id)
            assert parent is not None
            assert len(parent.text) <= 500
            assert child.text in parent.text

    async def test_multiple_parents_created(self):
        store = InMemoryParentStore()
        chunker = HierarchicalChunker(
            store, parent_chunk_size=500, parent_chunk_overlap=0
        )
        children = await chunker.chunk("doc-1", _document())
        parent_ids = {c.parent_id for c in children}
        assert len(parent_ids) > 1

    async def test_child_metadata_carries_parent_and_document_metadata(self):
        store = InMemoryParentStore()
        chunker = HierarchicalChunker(store)
        children = await chunker.chunk("doc-9", _document(50), {"source": "unit"})

        child = children[0]
        assert child.metadata["source"] == "unit"
        assert child.metadata["parent_id"] == child.parent_id
        assert child.metadata["parent_index"] == child.parent_index
        assert child.metadata["child_index"] == child.child_index
        parent = await store.get(child.parent_id)
        assert parent is not None
        assert parent.metadata["source"] == "unit"
        assert parent.metadata["document_id"] == "doc-9"

    async def test_empty_text_yields_nothing(self):
        store = InMemoryParentStore()
        chunker = HierarchicalChunker(store)
        assert await chunker.chunk("doc-1", "   ") == []


class TestExpandToParents:
    async def _store(self) -> tuple[InMemoryParentStore, str, str]:
        store = InMemoryParentStore()
        p0 = parent_chunk_id("doc", 0)
        p1 = parent_chunk_id("doc", 1)
        await store.put(p0, "parent zero", {"i": 0})
        await store.put(p1, "parent one", {"i": 1})
        return store, p0, p1

    async def test_dedupes_keeping_best_child_score(self):
        store, p0, p1 = await self._store()
        hits = [
            {"parent_id": p1, "score": 0.9},
            {"parent_id": p0, "score": 0.8},
            {"parent_id": p1, "score": 0.7},
        ]
        expanded = await expand_to_parents(hits, store)

        assert [(e.parent_id, e.text, e.score) for e in expanded] == [
            (p1, "parent one", 0.9),
            (p0, "parent zero", 0.8),
        ]

    async def test_orders_by_best_child_score(self):
        store, p0, p1 = await self._store()
        hits = [
            {"parent_id": p0, "score": 0.5},
            {"parent_id": p1, "score": 0.9},
        ]
        expanded = await expand_to_parents(hits, store)
        assert [e.parent_id for e in expanded] == [p1, p0]

    async def test_no_dedupe_preserves_hit_order(self):
        store, p0, p1 = await self._store()
        hits = [
            {"parent_id": p1, "score": 0.9},
            {"parent_id": p0, "score": 0.8},
            {"parent_id": p1, "score": 0.7},
        ]
        expanded = await expand_to_parents(hits, store, dedupe=False)
        assert [e.parent_id for e in expanded] == [p1, p0, p1]

    async def test_unknown_parent_skipped(self):
        store, p0, _ = await self._store()
        hits = [
            {"parent_id": "missing", "score": 0.9},
            {"parent_id": p0, "score": 0.8},
        ]
        expanded = await expand_to_parents(hits, store)
        assert [e.parent_id for e in expanded] == [p0]

    async def test_payload_shaped_hits(self):
        store, p0, _ = await self._store()
        hits = [{"payload": {"parent_id": p0}, "score": 0.5}]
        expanded = await expand_to_parents(hits, store)
        assert [e.parent_id for e in expanded] == [p0]

    async def test_search_result_hits(self):
        store, p0, _ = await self._store()
        hits = [
            SearchResult(
                document=Document(content="child", metadata={"parent_id": p0}),
                score=0.4,
            )
        ]
        expanded = await expand_to_parents(hits, store)
        assert [(e.parent_id, e.score) for e in expanded] == [(p0, 0.4)]

    async def test_expanded_parent_carries_metadata(self):
        store, p0, _ = await self._store()
        expanded = await expand_to_parents([{"parent_id": p0, "score": 1.0}], store)
        assert expanded[0].metadata == {"i": 0}
