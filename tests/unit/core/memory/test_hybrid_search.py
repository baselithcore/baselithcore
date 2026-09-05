"""Unit tests for ``core.memory.hybrid_search``."""

from __future__ import annotations

import math

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


class TestSearchWithExtra:
    """`search_with_extra` must score exactly like one fresh index over
    base ∪ extra — same df/idf/avgdl arithmetic — without rebuilding the
    base postings (the point: LTM candidates vary per query, and a rebuild
    per recall re-walks the whole STM/MTM corpus)."""

    BASE = {
        "a": "the quick brown fox jumps over errors",
        "b": "lazy dog naps in the warm sun",
        "c": "quick fixes for lazy code paths",
    }
    EXTRA = {
        "x": "vector databases index embeddings quick",
        "y": "unique zebra token appears here only",
    }
    QUERIES = (
        "quick fox",
        "lazy",
        "zebra",  # term existing ONLY in an extra doc
        "quick quick lazy",  # duplicate query terms keep multiplicity semantics
        "warm sun code embeddings",
        "absent-term",
    )

    def _unified(self):
        fresh = BM25Index()
        fresh.index({**self.BASE, **self.EXTRA})
        return fresh

    def _base(self):
        base = BM25Index()
        base.index(self.BASE)
        return base

    def test_exact_equivalence_with_unified_index(self) -> None:
        from core.memory.hybrid_search import bm25_doc_stats

        base = self._base()
        unified = self._unified()
        extra_tok = {d: bm25_doc_stats(t) for d, t in self.EXTRA.items()}
        for query in self.QUERIES:
            got = [
                (h.doc_id, h.score)
                for h in base.search_with_extra(query, extra_tok, top_k=10)
            ]
            want = [(h.doc_id, h.score) for h in unified.search(query, top_k=10)]
            assert got == want, query

    def test_empty_extra_delegates_to_plain_search(self) -> None:
        base = self._base()
        for query in self.QUERIES:
            got = [
                (h.doc_id, h.score) for h in base.search_with_extra(query, {}, top_k=10)
            ]
            want = [(h.doc_id, h.score) for h in base.search(query, top_k=10)]
            assert got == want

    def test_top_k_respected(self) -> None:
        from core.memory.hybrid_search import bm25_doc_stats

        base = self._base()
        extra_tok = {d: bm25_doc_stats(t) for d, t in self.EXTRA.items()}
        assert len(base.search_with_extra("quick lazy", extra_tok, top_k=1)) == 1


def _reference_ranking(
    idx: BM25Index, query: str, extra: dict[str, str] | None = None
) -> list[tuple[str, float]]:
    """Spec-level BM25 ranking: score every document, stable-sort descending.

    Deliberately the naive O(corpus) formulation the index optimizes away
    (dense score array, full sort, then truncate). It pins both the exact
    scores and the tie-break order — equal-scoring documents rank by corpus
    position — so the sparse/``heapq.nlargest`` fast path cannot silently
    reshuffle a ranking.
    """
    from core.memory.hybrid_search import _tokenize, bm25_doc_stats

    docs = dict(zip(idx._doc_ids, zip(idx._doc_freqs, idx._doc_lengths)))
    docs.update({d: bm25_doc_stats(t) for d, t in (extra or {}).items()})
    n_docs = len(docs)
    total_len = sum(length for _, length in docs.values())
    avgdl = (total_len / n_docs) if n_docs else 0.0

    scores = dict.fromkeys(docs, 0.0)
    for term in _tokenize(query):
        df_t = sum(1 for freqs, _ in docs.values() if term in freqs)
        if not df_t:
            continue
        idf = math.log(1 + (n_docs - df_t + 0.5) / (df_t + 0.5))
        for doc_id, (freqs, length) in docs.items():
            tf = freqs.get(term, 0)
            if not tf:
                continue
            norm = 1 - idx.b + idx.b * ((length or 1) / (avgdl or 1))
            scores[doc_id] += idf * (tf * (idx.k1 + 1)) / (tf + idx.k1 * norm)

    ranked = sorted(
        ((d, s) for d, s in scores.items() if s > 0), key=lambda p: p[1], reverse=True
    )
    return ranked


class TestSparseScoringEquivalence:
    """The sparse accumulator + ``nlargest`` must reproduce the naive ranking.

    Guards the hot-path rewrite: scoring only the documents in a query term's
    posting list, and heap-selecting ``top_k`` instead of sorting the whole
    corpus, may change neither the scores nor the order (ties included).
    """

    # Engineered for score ties: identical texts must tie exactly, and the
    # tie-break has to stay "earlier corpus position first".
    TIED = {f"t{i}": "alpha beta gamma" for i in range(6)}
    MIXED = {
        **TIED,
        "u1": "alpha beta gamma delta",
        "u2": "alpha",
        "u3": "beta gamma alpha alpha",
        "u4": "unrelated tokens entirely",
    }
    QUERIES = (
        "alpha",
        "alpha beta",
        "gamma delta",
        "alpha alpha beta",
        "unrelated",
        "nothing matches here",
        "",
    )

    @pytest.mark.parametrize("query", QUERIES)
    @pytest.mark.parametrize("top_k", [1, 2, 5, 100])
    def test_search_matches_naive_ranking(self, query: str, top_k: int) -> None:
        idx = BM25Index()
        idx.index(self.MIXED)
        got = [(h.doc_id, h.score) for h in idx.search(query, top_k=top_k)]
        assert got == _reference_ranking(idx, query)[:top_k]

    @pytest.mark.parametrize("query", QUERIES)
    @pytest.mark.parametrize("top_k", [1, 3, 100])
    def test_search_with_extra_matches_naive_ranking(
        self, query: str, top_k: int
    ) -> None:
        from core.memory.hybrid_search import bm25_doc_stats

        idx = BM25Index()
        idx.index(self.MIXED)
        extra = {"e1": "alpha beta gamma", "e2": "delta alpha"}
        got = [
            (h.doc_id, h.score)
            for h in idx.search_with_extra(
                query, {d: bm25_doc_stats(t) for d, t in extra.items()}, top_k=top_k
            )
        ]
        assert got == _reference_ranking(idx, query, extra)[:top_k]

    def test_all_tied_scores_keep_corpus_order(self) -> None:
        idx = BM25Index()
        idx.index(self.TIED)
        hits = idx.search("alpha beta gamma", top_k=6)
        assert [h.doc_id for h in hits] == list(self.TIED)
        assert len({h.score for h in hits}) == 1

    # Each query term hits a disjoint, later-positioned document pair, so the
    # sparse accumulator is *populated* in the order 2, 3, 0, 1 — deliberately
    # not corpus order. Same df and same length everywhere makes all four
    # scores identical, so the ranking is decided purely by the tie-break: it
    # must still come out in corpus order, as the naive full sort did.
    SHUFFLED_TIES = {
        "s0": "beta filler filler",
        "s1": "beta filler filler",
        "s2": "alpha filler filler",
        "s3": "alpha filler filler",
    }

    def test_ties_ignore_posting_list_insertion_order(self) -> None:
        idx = BM25Index()
        idx.index(self.SHUFFLED_TIES)
        hits = idx.search("alpha beta", top_k=4)
        assert len({round(h.score, 12) for h in hits}) == 1, "corpus must tie"
        assert [h.doc_id for h in hits] == ["s0", "s1", "s2", "s3"]
        assert [(h.doc_id, h.score) for h in hits] == _reference_ranking(
            idx, "alpha beta"
        )

    def test_extra_ties_never_outrank_equal_scoring_base_docs(self) -> None:
        """A tie between a base and an ``extra`` doc must keep the base first.

        ``extra`` docs are accumulated after the base postings for each term,
        so a term matching only ``extra`` populates the accumulator ahead of a
        base match — the previous code appended extra hits after base hits and
        stable-sorted, which put the base doc first.
        """
        from core.memory.hybrid_search import bm25_doc_stats

        idx = BM25Index()
        idx.index({"b0": "beta filler filler"})
        extra = {"e0": "alpha filler filler"}
        hits = idx.search_with_extra(
            "alpha beta", {d: bm25_doc_stats(t) for d, t in extra.items()}, top_k=2
        )
        assert len({round(h.score, 12) for h in hits}) == 1, "corpus must tie"
        assert [h.doc_id for h in hits] == ["b0", "e0"]

    def test_truncated_top_k_is_a_prefix_of_the_full_ranking(self) -> None:
        idx = BM25Index()
        idx.index(self.MIXED)
        full = idx.search("alpha beta gamma", top_k=len(self.MIXED))
        for k in range(1, len(full) + 1):
            assert idx.search("alpha beta gamma", top_k=k) == full[:k]

    def test_fuse_ties_keep_first_seen_order(self) -> None:
        # Mirrored rankings pair the docs into equal-RRF-score groups:
        # {d0, d3} (ranks 1+4) and {d1, d2} (ranks 2+3). Within each group the
        # order must follow first appearance in the bm25 stream.
        bm25 = [ScoredHit(f"d{i}", 1.0) for i in range(4)]
        dense = list(reversed(bm25))
        fused = HybridSearcher().fuse(bm25=bm25, dense=dense, top_k=4)
        assert len({round(h.score, 12) for h in fused}) == 2
        assert [h.doc_id for h in fused] == ["d0", "d3", "d1", "d2"]

    def test_fuse_truncation_is_a_prefix_of_the_full_fusion(self) -> None:
        bm25 = [ScoredHit(f"d{i}", 1.0) for i in range(6)]
        dense = [ScoredHit(f"d{i}", 1.0) for i in (3, 0, 5, 1)]
        full = HybridSearcher().fuse(bm25=bm25, dense=dense, top_k=6)
        for k in range(1, 7):
            assert HybridSearcher().fuse(bm25=bm25, dense=dense, top_k=k) == full[:k]

    def test_scoring_touches_only_posting_list_documents(self) -> None:
        """The whole point of the rewrite: cost tracks matches, not corpus size."""
        idx = BM25Index()
        corpus = {f"filler{i}": "common filler text" for i in range(2000)}
        corpus["rare"] = "singular needle token"
        idx.index(corpus)

        lengths_read: list[int] = []
        real_lengths = idx._doc_lengths

        class _Probe(list):
            def __getitem__(self, i):  # type: ignore[override]
                lengths_read.append(i)
                return real_lengths[i]

        idx._doc_lengths = _Probe(real_lengths)
        hits = idx.search("needle", top_k=5)
        assert [h.doc_id for h in hits] == ["rare"]
        # Exactly one document contains "needle"; nothing else may be scored.
        assert lengths_read == [idx._doc_ids.index("rare")]


class TestUnicodeTokenization:
    """The tokenizer canonicalizes before splitting: accented and non-Latin
    text must index and match, not be silently truncated or dropped."""

    def test_unaccented_query_matches_accented_document(self) -> None:
        index = BM25Index()
        index.index({"d1": "Perché la città è bella", "d2": "lazy dog naps"})
        hits = index.search("perche citta")
        assert [h.doc_id for h in hits] == ["d1"]

    def test_cjk_text_is_indexed(self) -> None:
        index = BM25Index()
        index.index({"d1": "東京タワー", "d2": "lazy dog naps"})
        hits = index.search("東京タワー")
        assert [h.doc_id for h in hits] == ["d1"]
