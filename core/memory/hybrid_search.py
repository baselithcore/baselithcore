"""
Hybrid retrieval: BM25 keyword index + Reciprocal Rank Fusion.

Vector-only search misses exact matches (error codes, identifiers, rare
terms); BM25-only misses semantic neighbours. Fusing both via Reciprocal
Rank Fusion (RRF) catches both classes of hit, and downstream
cross-encoder rerank lifts top-k precision further (the rerank step
already lives in ``core.chat.reranking``).

This module is dependency-light by design:

- ``BM25Index`` is a pure-Python BM25Okapi implementation; no external
  package required. Switch to ``rank_bm25`` later if profile shows a hot
  path; the public surface stays identical.
- ``HybridSearcher`` is a pure fuser: it accepts ranked lists from any
  source (vector store, keyword backend) and returns a fused ranking. It
  does not own the storage layer.

Usage::

    bm25 = BM25Index()
    bm25.index({"doc1": "the quick brown fox", "doc2": "lazy dog naps"})
    bm25_hits = bm25.search("quick fox", top_k=5)

    # ``dense_hits`` comes from an existing vector backend.
    fused = HybridSearcher().fuse(bm25=bm25_hits, dense=dense_hits, top_k=3)
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final

DEFAULT_BM25_K1: Final[float] = 1.5
DEFAULT_BM25_B: Final[float] = 0.75
DEFAULT_RRF_K: Final[int] = 60
DEFAULT_BM25_WEIGHT: Final[float] = 0.5
DEFAULT_DENSE_WEIGHT: Final[float] = 0.5

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase word-style tokenizer. Good-enough default for BM25."""
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def bm25_doc_stats(text: str) -> tuple[Counter[str], int]:
    """Per-document term frequencies + token count.

    The building block of :meth:`BM25Index.index_tokenized` — callers that
    rebuild an index over a mostly-stable corpus can cache these stats per
    document and skip re-tokenization. Treat the returned ``Counter`` as
    immutable once handed to an index.
    """
    tokens = _tokenize(text)
    return Counter(tokens), len(tokens)


@dataclass(frozen=True)
class ScoredHit:
    """A single ranked hit: ``doc_id`` plus its score in its source ranking."""

    doc_id: str
    score: float


@dataclass
class BM25Index:
    """In-memory BM25Okapi index. Build once, query many."""

    k1: float = DEFAULT_BM25_K1
    b: float = DEFAULT_BM25_B
    _doc_ids: list[str] = field(default_factory=list)
    _doc_freqs: list[Counter[str]] = field(default_factory=list)
    _doc_lengths: list[int] = field(default_factory=list)
    _avgdl: float = 0.0
    _idf: dict[str, float] = field(default_factory=dict)

    def index(self, docs: Mapping[str, str]) -> None:
        """Build the index from a ``doc_id -> text`` mapping."""
        self.index_tokenized({d: bm25_doc_stats(t) for d, t in docs.items()})

    def index_tokenized(self, docs: Mapping[str, tuple[Counter[str], int]]) -> None:
        """Build the index from pre-tokenized per-doc stats.

        ``docs`` maps ``doc_id -> (term_freqs, token_count)`` as produced by
        :func:`bm25_doc_stats`. Scoring is identical to :meth:`index`; this
        variant exists so callers can memoize tokenization across rebuilds.
        The passed ``Counter`` objects are stored by reference — do not
        mutate them afterwards.
        """
        self._doc_ids = list(docs.keys())
        self._doc_freqs = [docs[d][0] for d in self._doc_ids]
        self._doc_lengths = [docs[d][1] for d in self._doc_ids]
        n_docs = len(self._doc_ids)
        self._avgdl = (sum(self._doc_lengths) / n_docs) if n_docs > 0 else 0.0
        df: Counter[str] = Counter()
        for freqs in self._doc_freqs:
            df.update(freqs.keys())
        self._idf = {
            term: math.log(1 + (n_docs - df_t + 0.5) / (df_t + 0.5))
            for term, df_t in df.items()
        }

    def search(self, query: str, top_k: int = 10) -> list[ScoredHit]:
        """Return the top ``top_k`` documents for ``query``, descending score."""
        if not self._doc_ids:
            return []
        if top_k <= 0:
            raise ValueError("top_k must be > 0")
        terms = _tokenize(query)
        scores: list[float] = [0.0] * len(self._doc_ids)
        for term in terms:
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i, freqs in enumerate(self._doc_freqs):
                tf = freqs.get(term, 0)
                if tf == 0:
                    continue
                dl = self._doc_lengths[i] or 1
                norm = 1 - self.b + self.b * (dl / (self._avgdl or 1))
                scores[i] += idf * (tf * (self.k1 + 1)) / (tf + self.k1 * norm)
        ranked = sorted(
            (
                ScoredHit(doc_id=self._doc_ids[i], score=s)
                for i, s in enumerate(scores)
                if s > 0
            ),
            key=lambda h: h.score,
            reverse=True,
        )
        return ranked[:top_k]


@dataclass
class HybridSearcher:
    """Reciprocal Rank Fusion over independent ranked lists."""

    bm25_weight: float = DEFAULT_BM25_WEIGHT
    dense_weight: float = DEFAULT_DENSE_WEIGHT
    rrf_k: int = DEFAULT_RRF_K

    def __post_init__(self) -> None:
        if self.rrf_k <= 0:
            raise ValueError("rrf_k must be > 0")
        if self.bm25_weight < 0 or self.dense_weight < 0:
            raise ValueError("weights must be non-negative")

    def fuse(
        self,
        *,
        bm25: Iterable[ScoredHit] | None = None,
        dense: Iterable[ScoredHit] | None = None,
        top_k: int = 10,
    ) -> list[ScoredHit]:
        """Return the top ``top_k`` documents fused from both rankings."""
        if top_k <= 0:
            raise ValueError("top_k must be > 0")
        contributions: dict[str, float] = {}
        for stream, weight in (
            (bm25 or [], self.bm25_weight),
            (dense or [], self.dense_weight),
        ):
            if weight == 0:
                continue
            for rank, hit in enumerate(stream, start=1):
                contributions[hit.doc_id] = contributions.get(
                    hit.doc_id, 0.0
                ) + weight * (1.0 / (self.rrf_k + rank))
        ranked = sorted(
            (ScoredHit(doc_id=d, score=s) for d, s in contributions.items()),
            key=lambda h: h.score,
            reverse=True,
        )
        return ranked[:top_k]
