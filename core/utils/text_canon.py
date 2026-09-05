"""
Unicode canonicalization for keyword indexing, dedup keys and cache keys.

Two texts that differ only in accents, compatibility forms (ligatures,
fullwidth letters), letter case or whitespace must hash, tokenize and
deduplicate identically. Every consumer that keys on text content — the BM25
tokenizer, the memory-tier dedup key, the semantic cache's exact-match key —
funnels through this module so the rules stay in one place.

The pipeline mirrors the "compressed tokenizer" idea from lookup-based
conditional memory (NFKC → NFD → strip accents → lowercase → collapse
whitespace): canonicalize *before* hashing so surface variants of the same
words share an address.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

_WS_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
# Anything that is neither a word character nor whitespace: punctuation,
# symbols, quotes. Replaced by a space (a separator, never a glue) so
# "v1.2" keys as "v1 2" rather than merging into "v12".
_NON_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[^\w\s]+")


def canonicalize(text: str) -> str:
    """Return ``text`` in canonical form: NFKC, accent-stripped, casefolded,
    whitespace-collapsed.

    Punctuation is preserved — this is the form to *hash* or *tokenize*.
    Use :func:`canonical_key` when punctuation must not matter either.
    """
    if not text:
        return ""
    # NFKC folds compatibility forms (ligatures, fullwidth) into canonical
    # code points; NFD then splits base letters from combining marks so the
    # marks can be dropped; NFC recomposes what is left (Hangul jamo in
    # particular) so the output compares equal to normally typed text.
    decomposed = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    recomposed = unicodedata.normalize("NFC", stripped)
    return _WS_RE.sub(" ", recomposed.casefold()).strip()


def canonical_key(text: str) -> str:
    """:func:`canonicalize` plus punctuation removal — a near-duplicate key.

    ``"What is Python?"`` and ``"what is python"`` share a key; ``"v1.2"``
    and ``"v12"`` do not (punctuation separates, it never merges).
    """
    return _WS_RE.sub(" ", _NON_WORD_RE.sub(" ", canonicalize(text))).strip()


__all__ = ["canonical_key", "canonicalize"]
