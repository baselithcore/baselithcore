"""Tests for the /knowledge API and its upload extraction."""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import baselith.plugins.api_routers.routes.knowledge as knowledge_module
from baselith.core.api.errors import install_error_handlers
from baselith.core.auth.types import AuthRole, AuthUser
from baselith.core.config.knowledge import KnowledgeApiConfig
from baselith.core.middleware import require_user
from baselith.core.services.vectorstore.exceptions import VectorStoreError
from baselith.core.services.vectorstore.knowledge import (
    IngestError,
    IngestResult,
    KnowledgeHit,
)
from baselith.plugins.api_routers.routes import _knowledge_extract as extract

pytestmark = [pytest.mark.unit]

WRITER = AuthUser(user_id="w", roles={AuthRole.SCOPED}, scopes={"knowledge:write"})
READER = AuthUser(user_id="r", roles={AuthRole.SCOPED}, scopes={"knowledge:read"})
BOTH = AuthUser(
    user_id="b", roles={AuthRole.SCOPED}, scopes={"knowledge:read", "knowledge:write"}
)


def _config(**overrides: str) -> KnowledgeApiConfig:
    return KnowledgeApiConfig(KNOWLEDGE_API_ENABLED="true", **overrides)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    """A client plus the mocks standing in for the store-facing functions."""
    state: dict[str, Any] = {
        "user": BOTH,
        "config": _config(KNOWLEDGE_COLLECTIONS="faq,docs"),
    }

    ingest = AsyncMock(
        side_effect=lambda content, **kw: IngestResult(
            document_id=kw.get("document_id") or "generated",
            collection=kw.get("collection"),
            chunks=3,
            chunking=kw.get("chunking", "default"),
        )
    )
    delete = AsyncMock()
    search = AsyncMock(
        return_value=[
            KnowledgeHit("d1", 0, "alpha", 0.91, {"team": "support"}),
            KnowledgeHit("d2", None, "beta", 0.4, {}),
        ]
    )
    monkeypatch.setattr(knowledge_module, "ingest_document", ingest)
    monkeypatch.setattr(knowledge_module, "delete_document", delete)
    monkeypatch.setattr(knowledge_module, "search_by_text", search)
    monkeypatch.setattr(
        knowledge_module, "get_knowledge_config", lambda: state["config"]
    )

    app = FastAPI()
    app.include_router(knowledge_module.router)
    install_error_handlers(app)  # the app maps a missing scope to 403

    async def fake_require_user(request: Request) -> str:
        request.state.user = state["user"]
        return state["user"].user_id

    app.dependency_overrides[require_user] = fake_require_user
    client = TestClient(app)
    return SimpleApi(client, state, ingest, delete, search)


class SimpleApi:
    def __init__(self, client, state, ingest, delete, search):
        self.client, self.state = client, state
        self.ingest, self.delete, self.search = ingest, delete, search


class TestScopes:
    def test_read_only_identity_can_search_but_not_write(self, api):
        api.state["user"] = READER
        assert (
            api.client.post("/knowledge/search", json={"query": "q"}).status_code == 200
        )
        assert (
            api.client.post("/knowledge/documents", json={"content": "x"}).status_code
            == 403
        )
        assert api.client.delete("/knowledge/documents/doc").status_code == 403
        upload = api.client.post(
            "/knowledge/documents/upload", files={"file": ("a.txt", b"hello")}
        )
        assert upload.status_code == 403
        api.ingest.assert_not_awaited()
        api.delete.assert_not_awaited()

    def test_write_only_identity_cannot_search(self, api):
        api.state["user"] = WRITER
        assert (
            api.client.post("/knowledge/search", json={"query": "q"}).status_code == 403
        )
        api.search.assert_not_awaited()

    def test_role_defaults(self):
        from baselith.core.auth.scopes import ROLE_SCOPES

        assert "knowledge:read" in ROLE_SCOPES[AuthRole.USER]
        assert "knowledge:write" not in ROLE_SCOPES[AuthRole.USER]
        assert "knowledge:*" in ROLE_SCOPES[AuthRole.SERVICE]
        assert "knowledge:read" not in ROLE_SCOPES[AuthRole.GUEST]


class TestIngestText:
    def test_creates_a_document(self, api):
        resp = api.client.post(
            "/knowledge/documents",
            json={
                "content": "body",
                "document_id": "faq-1",
                "metadata": {"team": "support"},
                "chunking": "qa",
            },
        )
        assert resp.status_code == 201
        assert resp.json() == {
            "document_id": "faq-1",
            "collection": None,
            "chunks": 3,
            "chunking": "qa",
        }
        _, kwargs = api.ingest.call_args
        assert api.ingest.call_args.args == ("body",)
        assert kwargs["metadata"] == {"team": "support"} and kwargs["chunking"] == "qa"

    def test_an_exposed_collection_is_passed_through(self, api):
        resp = api.client.post(
            "/knowledge/documents", json={"content": "x", "collection": "faq"}
        )
        assert (
            resp.status_code == 201
            and api.ingest.call_args.kwargs["collection"] == "faq"
        )

    @pytest.mark.parametrize("name", ["memory", "FAQ", "../x", "faq/../memory", ""])
    def test_a_collection_that_is_not_exposed_is_404(self, api, name):
        resp = api.client.post(
            "/knowledge/documents", json={"content": "x", "collection": name}
        )
        assert resp.status_code == 404
        api.ingest.assert_not_awaited()

    def test_default_collection_only_when_nothing_is_exposed(self, api):
        api.state["config"] = _config()
        assert (
            api.client.post("/knowledge/documents", json={"content": "x"}).status_code
            == 201
        )
        resp = api.client.post(
            "/knowledge/documents", json={"content": "x", "collection": "faq"}
        )
        assert resp.status_code == 404

    def test_reserved_metadata_keys_are_refused_not_dropped(self, api):
        resp = api.client.post(
            "/knowledge/documents",
            json={"content": "x", "metadata": {"tenant_id": "other", "team": "a"}},
        )
        assert resp.status_code == 422 and "tenant_id" in resp.json()["detail"]
        api.ingest.assert_not_awaited()

    def test_metadata_bounds(self, api):
        many = {f"k{i}": 1 for i in range(51)}
        assert (
            api.client.post(
                "/knowledge/documents", json={"content": "x", "metadata": many}
            ).status_code
            == 422
        )
        big = {"k": "x" * (16 * 1024 + 1)}
        assert (
            api.client.post(
                "/knowledge/documents", json={"content": "x", "metadata": big}
            ).status_code
            == 422
        )
        long_key = {"k" * 101: 1}
        assert (
            api.client.post(
                "/knowledge/documents", json={"content": "x", "metadata": long_key}
            ).status_code
            == 422
        )

    def test_text_longer_than_the_limit_is_413(self, api):
        api.state["config"] = _config(KNOWLEDGE_MAX_TEXT_CHARS="1000")
        resp = api.client.post("/knowledge/documents", json={"content": "a" * 1001})
        assert resp.status_code == 413
        api.ingest.assert_not_awaited()

    def test_empty_content_and_unknown_mode_are_422(self, api):
        assert (
            api.client.post("/knowledge/documents", json={"content": ""}).status_code
            == 422
        )
        assert (
            api.client.post(
                "/knowledge/documents", json={"content": "x", "chunking": "magic"}
            ).status_code
            == 422
        )

    def test_ingest_errors_are_422_with_the_reason(self, api):
        api.ingest.side_effect = IngestError("no question/answer pairs found")
        resp = api.client.post(
            "/knowledge/documents", json={"content": "x", "chunking": "qa"}
        )
        assert (
            resp.status_code == 422
            and "no question/answer pairs" in resp.json()["detail"]
        )

    def test_store_failures_are_503_without_leaking_internals(self, api):
        api.ingest.side_effect = VectorStoreError(
            "postgres://user:secret@host/db refused"
        )
        resp = api.client.post("/knowledge/documents", json={"content": "x"})
        assert resp.status_code == 503
        assert "secret" not in resp.text and "postgres" not in resp.text


class TestUpload:
    def _post(self, api, name="notes.txt", data=b"hello world", **form):
        return api.client.post(
            "/knowledge/documents/upload",
            files={"file": (name, io.BytesIO(data))},
            data=form,
        )

    def test_text_file(self, api):
        resp = self._post(
            api, document_id="up-1", chunking="auto", metadata='{"team": "a"}'
        )
        assert resp.status_code == 201
        assert api.ingest.call_args.args == ("hello world",)
        kwargs = api.ingest.call_args.kwargs
        assert kwargs["document_id"] == "up-1" and kwargs["chunking"] == "auto"
        assert kwargs["metadata"] == {"filename": "notes.txt", "team": "a"}
        assert kwargs["source_name"] == "notes.txt"

    def test_the_client_filename_is_reduced_to_a_label(self, api):
        self._post(api, name="../../etc/passwd.txt")
        assert api.ingest.call_args.kwargs["metadata"]["filename"] == "passwd.txt"
        self._post(api, name="C:\\evil\\report.md")
        assert api.ingest.call_args.kwargs["metadata"]["filename"] == "report.md"

    def test_the_filename_cannot_override_the_stored_label_via_metadata_clash(
        self, api
    ):
        resp = self._post(api, metadata='{"tenant_id": "x"}')
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "name", ["tool.exe", "image.png", "archive.zip", "noextension", "page.html"]
    )
    def test_unsupported_extensions_are_415(self, api, name):
        assert self._post(api, name=name).status_code == 415
        api.ingest.assert_not_awaited()

    def test_binary_content_behind_a_text_name_is_415(self, api):
        assert self._post(api, data=b"\x00\x01\x02binary").status_code == 415

    def test_non_utf8_text_is_415(self, api):
        assert self._post(api, data="café".encode("latin-1")).status_code == 415

    def test_utf8_bom_is_stripped(self, api):
        self._post(api, data=b"\xef\xbb\xbfhello")
        assert api.ingest.call_args.args == ("hello",)

    def test_oversized_upload_is_413_before_extraction(self, api, monkeypatch):
        api.state["config"] = _config(KNOWLEDGE_MAX_UPLOAD_BYTES="1024")
        called = AsyncMock()
        monkeypatch.setattr(knowledge_module, "extract_text", called)
        assert self._post(api, data=b"a" * 2048).status_code == 413
        called.assert_not_awaited()

    @pytest.mark.parametrize("meta", ["not json", "[1, 2]", '"str"'])
    def test_metadata_must_be_a_json_object(self, api, meta):
        assert self._post(api, metadata=meta).status_code == 422

    def test_extracted_text_over_the_limit_is_413(self, api):
        api.state["config"] = _config(KNOWLEDGE_MAX_TEXT_CHARS="1000")
        assert self._post(api, data=b"a" * 1500).status_code == 413

    def test_rich_format_goes_through_the_document_reader(self, api, monkeypatch):
        seen = {}

        def read_pdf(path):
            seen["suffix"] = path.suffix
            seen["bytes"] = path.read_bytes()
            seen["mode"] = oct(path.stat().st_mode & 0o777)
            seen["name"] = path.name
            return "pdf text"

        monkeypatch.setattr(
            "baselith.plugins.document_sources.readers.read_pdf", read_pdf
        )
        resp = self._post(api, name="Annual Report.PDF", data=b"%PDF-fake")
        assert resp.status_code == 201
        assert api.ingest.call_args.args == ("pdf text",)
        assert seen == {
            "suffix": ".pdf",
            "bytes": b"%PDF-fake",
            "mode": "0o600",
            "name": "upload.pdf",
        }

    def test_a_reader_that_finds_no_text_is_422(self, api, monkeypatch):
        monkeypatch.setattr(
            "baselith.plugins.document_sources.readers.read_pdf", lambda p: None
        )
        assert self._post(api, name="scan.pdf", data=b"%PDF").status_code == 422

    def test_the_temp_file_is_removed(self, api, monkeypatch):
        import os

        paths = []
        monkeypatch.setattr(
            "baselith.plugins.document_sources.readers.read_pdf",
            lambda p: paths.append(p) or "text",
        )
        self._post(api, name="a.pdf", data=b"%PDF")
        assert (
            paths
            and not os.path.exists(paths[0])
            and not os.path.exists(paths[0].parent)
        )


class TestDelete:
    def test_delete_is_idempotent_and_scoped_to_the_named_collection(self, api):
        resp = api.client.delete("/knowledge/documents/faq-1?collection=faq")
        assert resp.status_code == 200 and resp.json() == {
            "document_id": "faq-1",
            "deleted": True,
        }
        api.delete.assert_awaited_once_with("faq-1", collection="faq")

    def test_hidden_collection_is_404(self, api):
        assert (
            api.client.delete("/knowledge/documents/x?collection=memory").status_code
            == 404
        )
        api.delete.assert_not_awaited()

    def test_invalid_id_is_422(self, api):
        api.delete.side_effect = IngestError("invalid document id")
        assert api.client.delete("/knowledge/documents/bad%20id").status_code == 422

    def test_store_failure_is_503(self, api):
        api.delete.side_effect = VectorStoreError("boom")
        assert api.client.delete("/knowledge/documents/x").status_code == 503


class TestSearch:
    def test_hit_testing_response(self, api):
        resp = api.client.post(
            "/knowledge/search",
            json={
                "query": "how?",
                "top_k": 7,
                "collection": "docs",
                "rerank": True,
                "min_score": 0.3,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert (
            body["query"] == "how?"
            and body["collection"] == "docs"
            and body["reranked"] is True
        )
        assert body["count"] == 2 and body["took_ms"] >= 0
        assert body["hits"][0] == {
            "document_id": "d1",
            "chunk_index": 0,
            "content": "alpha",
            "score": 0.91,
            "metadata": {"team": "support"},
        }
        assert api.search.call_args.kwargs == {
            "top_k": 7,
            "collection": "docs",
            "rerank": True,
            "min_score": 0.3,
        }

    def test_empty_result_is_a_real_answer(self, api):
        api.search.return_value = []
        body = api.client.post("/knowledge/search", json={"query": "nothing"}).json()
        assert body["count"] == 0 and body["hits"] == []

    @pytest.mark.parametrize(
        "payload",
        [
            {"query": ""},
            {"query": "x", "top_k": 0},
            {"query": "x", "top_k": 51},
            {"query": "x" * 4001},
            {},
        ],
    )
    def test_bounds(self, api, payload):
        assert api.client.post("/knowledge/search", json=payload).status_code == 422

    def test_store_failure_is_503_not_an_empty_list(self, api):
        api.search.side_effect = VectorStoreError("down")
        assert (
            api.client.post("/knowledge/search", json={"query": "q"}).status_code == 503
        )

    def test_hidden_collection_is_404(self, api):
        assert (
            api.client.post(
                "/knowledge/search", json={"query": "q", "collection": "memory"}
            ).status_code
            == 404
        )


class TestExtractHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("a.txt", "a.txt"),
            ("/etc/passwd", "passwd"),
            ("..\\..\\x.md", "x.md"),
            ("we\x00ird\nname.txt", "weirdname.txt"),
            ("", "upload"),
            (None, "upload"),
            ("../..", "upload"),
            ("x" * 400 + ".txt", ("x" * 400 + ".txt")[:200]),
        ],
    )
    def test_safe_filename(self, raw, expected):
        assert extract.safe_filename(raw) == expected

    async def test_read_capped_stops_at_the_limit(self):
        class Upload:
            def __init__(self, data):
                self._buf = io.BytesIO(data)
                self.read_total = 0

            async def read(self, n):
                chunk = self._buf.read(n)
                self.read_total += len(chunk)
                return chunk

        ok = Upload(b"a" * 100)
        assert await extract.read_capped(ok, 100) == b"a" * 100
        big = Upload(b"a" * 1_000_000)
        with pytest.raises(extract.UploadTooLarge):
            await extract.read_capped(big, 100)
        assert big.read_total <= 100 + 64 * 1024, (
            "it must not buffer the whole body first"
        )


def test_router_is_mounted_only_when_the_api_is_enabled(monkeypatch) -> None:
    import baselith.core.config.knowledge as knowledge_cfg
    from baselith.plugins.api_routers.plugin import ApiRoutersPlugin

    def paths(enabled: bool) -> set[str]:
        monkeypatch.setattr(
            knowledge_cfg,
            "_knowledge_config",
            KnowledgeApiConfig(KNOWLEDGE_API_ENABLED="true" if enabled else "false"),
        )
        app = FastAPI()
        for mounted in ApiRoutersPlugin().get_routers():
            app.include_router(mounted)
        return set(app.openapi()["paths"])

    on = paths(True)
    assert {
        "/v1/knowledge/documents",
        "/v1/knowledge/documents/upload",
        "/v1/knowledge/search",
    } <= on
    assert not any("knowledge" in p for p in paths(False))
