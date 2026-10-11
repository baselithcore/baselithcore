"""Behaviour of ``build_context_and_sources`` (baselith.core.chat.context).

The function turns reranked ``(hit, score)`` pairs into the prompt context and
the source list the UI renders. These tests pin the grouping, the per-document
limits, the score floor, the metadata block and the fallback that kicks in when
every hit would otherwise be filtered out.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from baselith.core.chat.context import (
    _build_context_block,
    _build_metadata,
    _extract_doc_source,
    _to_int,
    build_context_and_sources,
)

NL = "\n"
DNL = "\n\n"
SEP = "\n---\n"


class _Opaque:
    """A score that compares like a number but cannot be converted to one."""

    def __lt__(self, other: object) -> bool:
        return False


def _hit(payload: dict[str, Any] | None, hit_id: Any = "h") -> SimpleNamespace:
    return SimpleNamespace(payload=payload, id=hit_id)


def _build(hits: list[tuple[Any, Any]], **kwargs: Any) -> tuple[str, list[dict]]:
    params: dict[str, Any] = {
        "final_top_k": 5,
        "newline": NL,
        "double_newline": DNL,
        "section_separator": SEP,
    }
    params.update(kwargs)
    return build_context_and_sources(hits, **params)


class TestHelpers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(3, 3), ("7", 7), ("x", None), (None, None), (2.9, 2)],
    )
    def test_to_int(self, value, expected):
        assert _to_int(value) == expected

    def test_metadata_for_url_document(self):
        lines = _build_metadata(
            {"title": " Guide ", "url": " https://ex.com/a ", "origin": "web"}, NL
        )
        assert lines == ["Titolo: Guide", "Fonte: https://ex.com/a"]

    def test_metadata_falls_back_to_http_source(self):
        lines = _build_metadata({"source": "http://ex.com/s"}, NL)
        assert lines == ["Fonte: http://ex.com/s"]

    def test_metadata_ignores_non_http_source_and_blank_title(self):
        assert _build_metadata({"title": "  ", "source": "/tmp/x"}, NL) == []

    def test_metadata_for_filesystem_document(self):
        lines = _build_metadata(
            {
                "origin": " FileSystem ",
                "relative_path": "docs/a.md",
                "category": "docs",
                "doc_type": "markdown",
            },
            NL,
        )
        assert lines == ["Percorso: docs/a.md", "Cartella: docs", "Tipo: markdown"]

    def test_metadata_filesystem_with_blank_fields(self):
        payload = {"origin": "filesystem", "relative_path": " ", "category": 3}
        assert _build_metadata(payload, NL) == []

    def test_context_block_empty(self):
        assert _build_context_block([], newline=NL, double_newline=DNL) == ""

    def test_context_block_sorted_by_chunk_index(self):
        block = _build_context_block(
            [
                {"chunk_index": 2, "text": "second"},
                {"chunk_index": "bad", "text": "zero"},
                {"chunk_index": 1, "chunk_body": "first", "title": "T"},
            ],
            newline=NL,
            double_newline=DNL,
        )
        # "bad" sorts as index 0 and becomes the metadata source (no title).
        assert block == "zero\n\nfirst\n\nsecond"

    def test_context_block_metadata_only(self):
        block = _build_context_block(
            [{"title": "T", "text": "   "}], newline=NL, double_newline=DNL
        )
        assert block == "Titolo: T"

    def test_extract_source_empty(self):
        assert _extract_doc_source("d", [], 0.5) is None

    def test_extract_source_url_without_title_uses_url(self):
        info = _extract_doc_source("d", [{"url": " https://x ", "origin": "Web"}], 0.4)
        assert info == {
            "document_id": "d",
            "origin": "web",
            "score": 0.4,
            "title": "https://x",
            "url": "https://x",
            "source_type": "url",
        }

    def test_extract_source_blank_url_falls_through_to_http_source(self):
        info = _extract_doc_source(
            "d", [{"url": "  ", "source": "https://s", "title": "S"}], None
        )
        assert info == {
            "document_id": "d",
            "title": "S",
            "url": "https://s",
            "source_type": "url",
        }

    def test_extract_source_http_source_without_title(self):
        info = _extract_doc_source("d", [{"source": "http://s"}], None)
        assert info is not None and info["title"] == "http://s"

    def test_extract_source_filesystem_prefers_relative_path_and_title(self):
        info = _extract_doc_source(
            "d",
            [{"origin": "filesystem", "relative_path": "a/b.md", "title": "B"}],
            1.0,
        )
        assert info is not None
        assert (info["path"], info["title"], info["source_type"]) == (
            "a/b.md",
            "B",
            "path",
        )

    def test_extract_source_filesystem_uses_source_and_filename(self):
        info = _extract_doc_source(
            "d",
            [{"origin": "filesystem", "source": "/srv/b.md", "filename": "b.md"}],
            None,
        )
        assert info is not None
        assert (info["path"], info["title"]) == ("/srv/b.md", "b.md")

    def test_extract_source_filesystem_title_defaults_to_location(self):
        info = _extract_doc_source(
            "d", [{"origin": "filesystem", "relative_path": "x.txt"}], None
        )
        assert info is not None and info["title"] == "x.txt"

    def test_extract_source_filesystem_without_location(self):
        assert _extract_doc_source("d", [{"origin": "filesystem"}], None) is None

    def test_extract_source_unknown_origin(self):
        assert _extract_doc_source("d", [{"origin": "s3", "text": "t"}], None) is None


class TestBuildContextAndSources:
    def test_empty_input(self):
        assert _build([]) == ("", [])

    def test_groups_chunks_per_document_and_annotates_sources(self):
        hits = [
            (_hit({"document_id": "a", "chunk_index": 0, "text": "A0", "url": "https://a"}), 0.9),
            (_hit({"document_id": "b", "chunk_index": 0, "text": "BB0", "origin": "filesystem", "relative_path": "b.md"}), 0.8),
            (_hit({"document_id": "a", "chunk_index": 1, "text": "A1"}), 0.7),
        ]  # fmt: skip
        context, sources = _build(hits, max_chunks_per_document=3)

        blocks = context.split(SEP)
        assert blocks == ["Fonte: https://a\n\nA0\n\nA1", "Percorso: b.md\n\nBB0"]
        a, b = sources
        assert a["document_id"] == "a" and a["score"] == 0.9
        assert a["chunks_used"] == 2
        assert a["chunk_coverage_ratio"] == pytest.approx(2 / 3)
        assert a["score_avg"] == pytest.approx(0.8)
        assert a["context_chars"] == 4
        assert a["context_ratio"] == pytest.approx(4 / 7)
        assert b["source_type"] == "path" and b["context_chars"] == 3

    def test_hit_without_payload_keys_on_hit_id(self):
        context, sources = _build([(_hit(None, hit_id=42), 0.9)])
        assert context == ""
        assert sources == []

    def test_score_floor_skips_new_documents_after_the_first(self):
        hits = [
            (_hit({"document_id": "a", "text": "keep", "url": "https://a"}), 0.05),
            (_hit({"document_id": "b", "text": "drop", "url": "https://b"}), 0.05),
        ]
        context, sources = _build(hits, min_rerank_score=0.15)
        # The first document is always admitted, even below the floor.
        assert context == "Fonte: https://a\n\nkeep"
        assert [s["document_id"] for s in sources] == ["a"]

    def test_final_top_k_caps_documents(self):
        hits = [
            (_hit({"document_id": d, "text": d, "url": f"https://{d}"}), 0.9)
            for d in "abc"
        ]
        _, sources = _build(hits, final_top_k=2)
        assert [s["document_id"] for s in sources] == ["a", "b"]

    def test_duplicate_and_distant_chunks_are_dropped(self):
        hits = [
            (_hit({"document_id": "a", "chunk_index": 5, "text": "five"}), 0.9),
            (_hit({"document_id": "a", "chunk_index": 5, "text": "dupe"}), 0.9),
            (_hit({"document_id": "a", "chunk_index": 9, "text": "far"}), 0.9),
            (_hit({"document_id": "a", "chunk_index": 6, "text": "six"}), 0.9),
        ]
        context, _ = _build(hits)
        assert context == "five\n\nsix"

    def test_max_chunks_per_document(self):
        hits = [
            (_hit({"document_id": "a", "chunk_index": i, "text": f"c{i}"}), 0.9)
            for i in range(3)
        ]
        context, _ = _build(hits, max_chunks_per_document=2)
        assert context == "c0\n\nc1"

    def test_primary_index_set_by_first_indexed_chunk(self):
        hits = [
            (_hit({"document_id": "a", "text": "no-index"}), 0.9),
            (_hit({"document_id": "a", "chunk_index": 10, "text": "ten"}), 0.9),
            (_hit({"document_id": "a", "chunk_index": 20, "text": "far"}), 0.9),
        ]
        context, _ = _build(hits)
        assert "ten" in context and "far" not in context

    def test_best_score_wins_and_average_covers_every_chunk(self):
        hits = [
            (_hit({"document_id": "a", "chunk_index": 0, "text": "x", "url": "https://a"}), 0.4),
            (_hit({"document_id": "a", "chunk_index": 1, "text": "y"}), 0.6),
            (_hit({"document_id": "a", "chunk_index": 2, "text": "z"}), 0.2),
        ]  # fmt: skip
        _, sources = _build(hits)
        assert sources[0]["score"] == 0.6
        assert sources[0]["score_avg"] == pytest.approx(0.4)

    def test_non_numeric_score_is_tolerated(self):
        hits = [
            (_hit({"document_id": "c", "text": "C", "url": "https://c"}), _Opaque())
        ]
        context, sources = _build(hits)
        assert context == "Fonte: https://c\n\nC"
        assert "score" not in sources[0]
        assert "score_avg" not in sources[0]

    def test_fallback_readmits_hits_the_primary_pass_filtered(self):
        # Pass 1 keeps only "a" (empty text, no metadata) and "d" (empty):
        # "b" falls under the score floor, so no block survives and the
        # fallback re-admits the raw hits without the floor.
        hits = [
            (_hit({"document_id": "a", "chunk_index": 0, "text": " "}), 0.3),
            (_hit({"document_id": "a", "chunk_index": 1, "text": ""}), 0.5),
            (_hit({"document_id": "b", "text": "B", "url": "https://b"}), 0.01),
            (_hit({"document_id": "d", "text": ""}), _Opaque()),
        ]
        context, sources = _build(hits, final_top_k=4, max_chunks_per_document=1)
        assert context == "Fonte: https://b\n\nB"
        assert sources == [
            {
                "document_id": "b",
                "score": 0.01,
                "title": "https://b",
                "url": "https://b",
                "source_type": "url",
                "chunks_used": 1,
                "chunk_coverage_ratio": 1.0,
                "score_avg": pytest.approx(0.01),
                "context_chars": 1,
                "context_ratio": 1.0,
            }
        ]
