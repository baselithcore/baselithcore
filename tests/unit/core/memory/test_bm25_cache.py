"""
Tests for BM25 memoization in hierarchical memory recall.

The cache must be a pure speedup: scoring identical to a fresh build,
whole-index reuse on an unchanged corpus, correct invalidation on change.
"""

from core.memory.hierarchy_search import HierarchySearchMixin
from core.memory.hybrid_search import BM25Index, bm25_doc_stats

DOCS = {
    "a": "the quick brown fox jumps over errors",
    "b": "lazy dog naps in the warm sun",
    "c": "quick fixes for lazy code paths",
}

QUERIES = ("quick fox", "lazy", "warm sun code", "absent-term")


def _hits(index: BM25Index, query: str) -> list[tuple[str, float]]:
    return [(h.doc_id, h.score) for h in index.search(query, top_k=10)]


def test_index_tokenized_matches_index_exactly():
    plain = BM25Index()
    plain.index(DOCS)

    pre = BM25Index()
    pre.index_tokenized({d: bm25_doc_stats(t) for d, t in DOCS.items()})

    for query in QUERIES:
        assert _hits(pre, query) == _hits(plain, query)


def test_build_bm25_index_reuses_whole_index_on_identical_corpus():
    mixin = HierarchySearchMixin()
    first = mixin._build_bm25_index(dict(DOCS))
    second = mixin._build_bm25_index(dict(DOCS))
    assert second is first


def test_build_bm25_index_invalidates_on_content_change():
    mixin = HierarchySearchMixin()
    first = mixin._build_bm25_index(dict(DOCS))

    changed = dict(DOCS, c="completely different replacement text")
    rebuilt = mixin._build_bm25_index(changed)
    assert rebuilt is not first

    # Scoring after a partial rebuild (a/b token stats came from the cache)
    # must equal a from-scratch build over the same corpus.
    fresh = BM25Index()
    fresh.index(changed)
    for query in ("quick fox", "lazy", "replacement"):
        assert _hits(rebuilt, query) == _hits(fresh, query)


def test_build_bm25_index_drops_evicted_documents():
    mixin = HierarchySearchMixin()
    mixin._build_bm25_index(dict(DOCS))

    shrunk = {"a": DOCS["a"]}
    index = mixin._build_bm25_index(shrunk)
    assert {h.doc_id for h in index.search("lazy", top_k=10)} == set()
    assert set(mixin._bm25_stats_cache) == {"a"}
