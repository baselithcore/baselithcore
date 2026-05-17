"""Unit tests for ``core.memory.hybrid_search``."""

from __future__ import annotations

import pytest

from core.memory.hybrid_search import (
    BM25Index,
    HybridSearcher,
    ScoredHit,
)


CORPUS = {
    "d1": "the quick brown fox jumps over the lazy dog",
    "d2": "an idle dog sleeps under the warm sun",
    "d3": "python error ERR_742 caused database connection failure",
    "d4": "fast brown foxes outrun lazy dogs in the meadow",
    "d5": "machine learning models predict outcomes from data",
}


class TestBM25Index:
    def test_empty_index_returns_empty(self) -> None:
        idx = BM25Index()
        assert idx.search("anything") == []

    def test_exact_term_match_ranks_first(self) -> None:
        idx = BM25Index()
        idx.index(CORPUS)
        hits = idx.search("ERR_742", top_k=3)
        assert hits
        assert hits[0].doc_id == "d3"

    def test_multiterm_query_aggregates_score(self) -> None:
        idx = BM25Index()
        idx.index(CORPUS)
        hits = idx.search("brown fox", top_k=5)
        ids = [h.doc_id for h in hits]
        assert "d1" in ids
        assert "d4" in ids
        assert "d5" not in ids

    def test_top_k_respects_limit(self) -> None:
        idx = BM25Index()
        idx.index(CORPUS)
        hits = idx.search("dog", top_k=2)
        assert len(hits) <= 2

    def test_unknown_query_returns_empty(self) -> None:
        idx = BM25Index()
        idx.index(CORPUS)
        assert idx.search("zzzz qqqq") == []

    def test_top_k_zero_rejected(self) -> None:
        idx = BM25Index()
        idx.index(CORPUS)
        with pytest.raises(ValueError):
            idx.search("dog", top_k=0)

    def test_scores_descending(self) -> None:
        idx = BM25Index()
        idx.index(CORPUS)
        hits = idx.search("brown fox jumps", top_k=5)
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)


class TestHybridSearcher:
    def _hits(self, ids: list[str]) -> list[ScoredHit]:
        return [ScoredHit(doc_id=d, score=10.0 - i) for i, d in enumerate(ids)]

    def test_fuse_balances_both_streams(self) -> None:
        f = HybridSearcher().fuse(
            bm25=self._hits(["a", "b", "c"]),
            dense=self._hits(["c", "b", "d"]),
            top_k=4,
        )
        ids = [h.doc_id for h in f]
        assert ids[:2] == ["b", "c"] or ids[:2] == ["c", "b"]
        assert "a" in ids
        assert "d" in ids

    def test_fuse_respects_top_k(self) -> None:
        f = HybridSearcher().fuse(
            bm25=self._hits(["a", "b", "c", "d", "e"]),
            dense=self._hits(["e", "d", "c", "b", "a"]),
            top_k=2,
        )
        assert len(f) == 2

    def test_zero_weight_skips_stream(self) -> None:
        f = HybridSearcher(bm25_weight=0.0, dense_weight=1.0).fuse(
            bm25=self._hits(["bm1", "bm2"]),
            dense=self._hits(["d1", "d2"]),
            top_k=3,
        )
        ids = [h.doc_id for h in f]
        assert "bm1" not in ids and "bm2" not in ids
        assert "d1" in ids and "d2" in ids

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValueError):
            HybridSearcher(bm25_weight=-0.1)

    def test_rrf_k_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            HybridSearcher(rrf_k=0)

    def test_empty_streams_returns_empty(self) -> None:
        assert HybridSearcher().fuse(bm25=[], dense=[], top_k=5) == []

    def test_top_k_zero_rejected(self) -> None:
        with pytest.raises(ValueError):
            HybridSearcher().fuse(bm25=[], dense=[], top_k=0)

    def test_single_stream_works(self) -> None:
        f = HybridSearcher().fuse(bm25=self._hits(["x", "y"]), top_k=2)
        ids = [h.doc_id for h in f]
        assert ids == ["x", "y"]

    def test_higher_rank_gets_higher_contribution(self) -> None:
        f = HybridSearcher(bm25_weight=1.0, dense_weight=0.0).fuse(
            bm25=self._hits(["first", "second", "third"]),
            top_k=3,
        )
        assert [h.doc_id for h in f] == ["first", "second", "third"]
        assert f[0].score > f[1].score > f[2].score


class TestEndToEndIntegration:
    def test_bm25_results_fused_with_synthetic_dense(self) -> None:
        idx = BM25Index()
        idx.index(CORPUS)
        bm25_hits = idx.search("brown fox", top_k=5)
        dense_hits = [
            ScoredHit(doc_id="d4", score=0.91),
            ScoredHit(doc_id="d1", score=0.89),
            ScoredHit(doc_id="d2", score=0.40),
        ]
        fused = HybridSearcher().fuse(bm25=bm25_hits, dense=dense_hits, top_k=3)
        ids = [h.doc_id for h in fused]
        assert "d1" in ids
        assert "d4" in ids
