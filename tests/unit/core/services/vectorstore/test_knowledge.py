"""Text-level knowledge helpers shared by the HTTP API, MCP and workflows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from baselith.core.context import reset_tenant_context, set_tenant_context
from baselith.core.models.domain import Document, SearchResult
from baselith.core.services.vectorstore import knowledge
from baselith.core.services.vectorstore.chunking import chunk_point_id, chunk_text
from baselith.core.services.vectorstore.exceptions import VectorStoreError
from baselith.core.services.vectorstore.knowledge import (
    IngestError,
    delete_document,
    ingest_document,
    search_by_text,
)

pytestmark = [pytest.mark.unit]

EMBEDDER = object()


class FakeStore:
    """Just enough of VectorStoreService to observe what ingestion does."""

    def __init__(self, *, previous_chunks=None, index_result=1, retrieve_error=None):
        self.config = SimpleNamespace(collection_name="kb")
        self.provider = SimpleNamespace(delete=AsyncMock())
        self.indexed: list[tuple[Document, dict]] = []
        self.deleted_documents: list[str] = []
        self._previous = previous_chunks
        self._index_result = index_result
        self._retrieve_error = retrieve_error
        self.retrieve_calls: list[list] = []

    async def retrieve(self, point_ids, collection_name=None, **kwargs):
        self.retrieve_calls.append(list(point_ids))
        if self._retrieve_error:
            raise self._retrieve_error
        if self._previous is None:
            return []
        return [
            SimpleNamespace(
                payload={"document_id": "doc", "chunk_count": self._previous}
            )
        ]

    async def index(self, documents, collection_name=None, embedder=None, **kwargs):
        for document in documents:
            self.indexed.append(
                (document, {"collection": collection_name, "embedder": embedder})
            )
        return self._index_result

    async def delete_document(self, document_id, collection_name=None, **kwargs):
        self.deleted_documents.append(document_id)


LONG_TEXT = "\n\n".join(f"Paragraph {i}. " + "word " * 120 for i in range(8))


class TestIngest:
    async def test_default_mode_lets_the_pipeline_chunk(self):
        store = FakeStore()
        result = await ingest_document(
            LONG_TEXT,
            document_id="doc",
            collection="kb",
            service=store,
            embedder=EMBEDDER,
        )
        ((document, kwargs),) = store.indexed
        assert "ingestion_chunks" not in document.metadata
        assert kwargs == {"collection": "kb", "embedder": EMBEDDER}
        assert result.chunks == len(chunk_text(LONG_TEXT)) > 1
        assert (result.document_id, result.chunking) == ("doc", "default")

    async def test_qa_mode_embeds_the_question(self):
        store = FakeStore()
        result = await ingest_document(
            "Q: Why?\nA: Because.\nQ: Who?\nA: Me.",
            document_id="doc",
            chunking="qa",
            service=store,
            embedder=EMBEDDER,
        )
        chunks = store.indexed[0][0].metadata["ingestion_chunks"]
        assert [c["embedding_text"] for c in chunks] == ["Why?", "Who?"]
        assert result.chunks == 2

    async def test_qa_mode_refuses_prose_instead_of_chunking_it(self):
        store = FakeStore()
        with pytest.raises(IngestError, match="no question/answer pairs"):
            await ingest_document(
                "plain prose",
                document_id="doc",
                chunking="qa",
                service=store,
                embedder=EMBEDDER,
            )
        assert store.indexed == []

    async def test_auto_mode_follows_markdown_structure(self):
        store = FakeStore()
        text = "# Title\n\nIntro.\n\n## Setup\n\nInstall it.\n"
        await ingest_document(
            text,
            document_id="doc",
            chunking="auto",
            source_name="guide.md",
            service=store,
            embedder=EMBEDDER,
        )
        chunks = store.indexed[0][0].metadata["ingestion_chunks"]
        assert any(c.get("headings") == ["Title", "Setup"] for c in chunks)

    async def test_auto_mode_on_plain_text_is_the_default_path(self):
        store = FakeStore()
        await ingest_document(
            "just words",
            document_id="doc",
            chunking="auto",
            source_name="a.txt",
            service=store,
            embedder=EMBEDDER,
        )
        assert "ingestion_chunks" not in store.indexed[0][0].metadata

    async def test_caller_metadata_is_kept_and_not_mutated(self):
        store = FakeStore()
        meta = {"team": "support"}
        await ingest_document(
            "hello", document_id="doc", metadata=meta, service=store, embedder=EMBEDDER
        )
        assert store.indexed[0][0].metadata == {"team": "support"} and meta == {
            "team": "support"
        }

    async def test_generated_ids_are_valid_and_unique(self):
        store = FakeStore()
        a = await ingest_document("one", service=store, embedder=EMBEDDER)
        b = await ingest_document("two", service=store, embedder=EMBEDDER)
        assert a.document_id != b.document_id and knowledge.DOCUMENT_ID_PATTERN.match(
            a.document_id
        )

    @pytest.mark.parametrize("bad", ["", " x", "-x", "a/b", "a b", "x" * 201, "../etc"])
    async def test_invalid_ids_are_refused(self, bad):
        with pytest.raises(IngestError, match="invalid document id"):
            await ingest_document(
                "hello", document_id=bad, service=FakeStore(), embedder=EMBEDDER
            )

    @pytest.mark.parametrize("content", ["", "   \n\t"])
    async def test_empty_content_is_refused(self, content):
        with pytest.raises(IngestError, match="empty"):
            await ingest_document(
                content, document_id="doc", service=FakeStore(), embedder=EMBEDDER
            )

    async def test_unknown_mode_is_refused(self):
        with pytest.raises(IngestError, match="unknown chunking mode"):
            await ingest_document(
                "hi",
                document_id="doc",
                chunking="semantic",
                service=FakeStore(),
                embedder=EMBEDDER,
            )

    async def test_a_store_that_writes_nothing_is_an_error(self):
        store = FakeStore(index_result=0)
        with pytest.raises(IngestError, match="wrote nothing"):
            await ingest_document(
                "hello", document_id="doc", service=store, embedder=EMBEDDER
            )


class TestReplace:
    async def test_a_shorter_new_version_trims_the_old_tail_after_writing(self):
        store = FakeStore(previous_chunks=6)
        result = await ingest_document(
            "Q: a?\nA: 1\nQ: b?\nA: 2",
            document_id="doc",
            chunking="qa",
            service=store,
            embedder=EMBEDDER,
        )
        assert result.chunks == 2
        call = store.provider.delete.call_args.kwargs
        assert call["collection_name"] == "kb"
        assert call["point_ids"] == [
            chunk_point_id("doc", i, "default") for i in range(2, 6)
        ]

    async def test_trimmed_ids_are_the_tenants_own(self):
        store = FakeStore(previous_chunks=3)
        token = set_tenant_context("tenant-a")
        try:
            await ingest_document(
                "hello", document_id="doc", service=store, embedder=EMBEDDER
            )
        finally:
            reset_tenant_context(token)
        ids = store.provider.delete.call_args.kwargs["point_ids"]
        assert ids == [chunk_point_id("doc", i, "tenant-a") for i in (1, 2)]
        assert not set(ids) & {chunk_point_id("doc", i) for i in (1, 2)}

    async def test_nothing_is_trimmed_when_the_new_version_is_not_shorter(self):
        store = FakeStore(previous_chunks=1)
        await ingest_document(
            "hello", document_id="doc", service=store, embedder=EMBEDDER
        )
        store.provider.delete.assert_not_awaited()

    async def test_a_failed_write_leaves_the_old_version_alone(self):
        store = FakeStore(previous_chunks=5, index_result=0)
        with pytest.raises(IngestError):
            await ingest_document(
                "hello", document_id="doc", service=store, embedder=EMBEDDER
            )
        store.provider.delete.assert_not_awaited()

    async def test_replace_false_skips_the_lookup_and_the_trim(self):
        store = FakeStore(previous_chunks=5)
        await ingest_document(
            "hello", document_id="doc", replace=False, service=store, embedder=EMBEDDER
        )
        assert store.retrieve_calls == [] and not store.provider.delete.await_count

    async def test_an_unreadable_previous_version_does_not_block_ingestion(self):
        store = FakeStore(retrieve_error=VectorStoreError("collection missing"))
        result = await ingest_document(
            "hello", document_id="doc", service=store, embedder=EMBEDDER
        )
        assert result.chunks == 1 and len(store.indexed) == 1

    async def test_a_foreign_payload_is_not_trusted_for_the_old_length(self):
        store = FakeStore(previous_chunks=9)
        store._previous = 9

        async def retrieve(point_ids, collection_name=None, **kw):
            return [SimpleNamespace(payload={"document_id": "other", "chunk_count": 9})]

        store.retrieve = retrieve
        await ingest_document(
            "hello", document_id="doc", service=store, embedder=EMBEDDER
        )
        store.provider.delete.assert_not_awaited()


class TestSearch:
    @staticmethod
    def _result(doc_id, score, **payload):
        document = Document(
            id=doc_id,
            content=payload.pop("text", "body"),
            metadata={"document_id": doc_id, **payload},
        )
        return SearchResult(document=document, score=score)

    @pytest.fixture
    def patched(self, monkeypatch):
        seen = {}

        async def encode(embedder, texts):
            seen["texts"] = texts
            return [[0.5, 0.25]]

        monkeypatch.setattr("baselith.core.nlp.roles.aencode_query", encode)
        return seen

    async def test_hits_are_shaped_and_internals_hidden(self, patched):
        class Store:
            async def search(self, **kwargs):
                patched["search"] = kwargs
                return [
                    TestSearch._result(
                        "d1",
                        0.9,
                        text="alpha",
                        chunk_index=2,
                        tenant_id="t",
                        chunk_body="x",
                        team="support",
                    ),
                    TestSearch._result("d2", 0.2, text="beta"),
                ]

        hits = await search_by_text(
            "q",
            top_k=4,
            collection="kb",
            rerank=True,
            service=Store(),
            embedder=EMBEDDER,
        )
        assert patched["texts"] == ["q"]
        assert patched["search"] == {
            "query_vector": [0.5, 0.25],
            "k": 4,
            "collection_name": "kb",
            "query_text": "q",
            "rerank": True,
        }
        first = hits[0].to_dict()
        assert (
            first["document_id"] == "d1"
            and first["chunk_index"] == 2
            and first["content"] == "alpha"
        )
        assert first["metadata"] == {
            "document_id": "d1",
            "chunk_index": 2,
            "team": "support",
        }
        assert hits[1].chunk_index is None

    async def test_min_score_filters(self, patched):
        class Store:
            async def search(self, **kwargs):
                return [TestSearch._result("d1", 0.9), TestSearch._result("d2", 0.2)]

        hits = await search_by_text(
            "q", min_score=0.5, service=Store(), embedder=EMBEDDER
        )
        assert [h.document_id for h in hits] == ["d1"]

    async def test_store_failures_are_not_an_empty_answer(self, patched):
        class Store:
            async def search(self, **kwargs):
                raise VectorStoreError("down")

        with pytest.raises(VectorStoreError):
            await search_by_text("q", service=Store(), embedder=EMBEDDER)


class TestDelete:
    async def test_deletes_through_the_service(self):
        store = FakeStore()
        await delete_document("doc", collection="kb", service=store)
        assert store.deleted_documents == ["doc"]

    async def test_invalid_id_never_reaches_the_store(self):
        store = FakeStore()
        with pytest.raises(IngestError):
            await delete_document("a/b", service=store)
        assert store.deleted_documents == []


async def test_resolve_embedder_builds_off_the_event_loop(monkeypatch):
    import threading

    built = {}

    def get_embedder():
        built["thread"] = threading.current_thread()
        return EMBEDDER

    monkeypatch.setattr("baselith.core.nlp.models.get_embedder", get_embedder)
    assert await knowledge.resolve_embedder() is EMBEDDER
    assert built["thread"] is not threading.main_thread()
