"""
Word n-gram fingerprints: the static-lookup tier of the semantic cache.

Sits between the exact-match key and the embedding scan. A prompt is reduced
to the set of its canonicalized word unigrams and bigrams; two prompts whose
sets overlap (Jaccard) above a threshold are near-verbatim variants of each
other — punctuation, stop-word or filler differences — and can share a
cached response without paying an embedder call (tens of milliseconds).

Bigrams keep word *order* in the picture: "translate english to italian" and
"translate italian to english" share every unigram but no bigram, so the
tier never conflates them and the lookup falls through to the embedding
scan. The design borrows from lookup-based conditional memory (deterministic
n-gram addressing over a compressed token stream), applied at the runtime
cache rather than inside the model.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, TypeVar

from core.utils.text_canon import canonical_key

#: Unigrams + bigrams. Longer shingles make short prompts too sparse to match.
DEFAULT_MAX_NGRAM: Final[int] = 2

T = TypeVar("T")


def ngram_fingerprint(text: str, *, max_n: int = DEFAULT_MAX_NGRAM) -> frozenset[str]:
    """Set of canonicalized word n-grams (1..``max_n``) of ``text``.

    Canonicalization (:func:`core.utils.text_canon.canonical_key`) runs first,
    so case, accents, punctuation and whitespace never change the fingerprint.
    """
    tokens = canonical_key(text).split()
    shingles: set[str] = set()
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            shingles.add(" ".join(tokens[i : i + n]))
    return frozenset(shingles)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity ``|a ∩ b| / |a ∪ b|``; ``0.0`` when nothing overlaps
    (two empty fingerprints included — an empty prompt matches nothing)."""
    intersection = len(a & b)
    if intersection == 0:
        return 0.0
    return intersection / (len(a) + len(b) - intersection)


def best_fingerprint_match(
    query: frozenset[str],
    candidates: Iterable[tuple[T, frozenset[str]]],
    *,
    threshold: float,
) -> tuple[T, float] | None:
    """Best-scoring candidate at or above ``threshold``, or ``None``.

    Ties keep the first candidate seen (insertion order = oldest entry).
    """
    best: T | None = None
    best_score = 0.0
    for candidate, fingerprint in candidates:
        score = jaccard(query, fingerprint)
        if score >= threshold and score > best_score:
            best, best_score = candidate, score
    return None if best is None else (best, best_score)


__all__ = [
    "DEFAULT_MAX_NGRAM",
    "best_fingerprint_match",
    "jaccard",
    "ngram_fingerprint",
]
