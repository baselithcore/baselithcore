"""Inverted match index for explicit document mentions.

``_inject_explicit_doc_matches`` historically linear-scanned every indexed
document per chat request, running three substring checks plus a stem lookup
each — O(corpus) event-loop CPU on the hot path. This module replaces the
scan with:

* an **Aho–Corasick automaton** over every document's lowered title /
  filename / relative path — one pass over the query text finds every
  document whose field is a substring of the query, with semantics *exactly*
  equal to the per-document ``field in query_l`` checks it replaces
  (including mid-word matches);
* a **stem → documents inverted map** for the token-level stem check.

Per-request cost drops to O(len(query) + matches). The index is cached and
rebuilt only when the corpus snapshot (doc id → raw metadata triple)
changes — the same invalidation contract as the sibling ``_doc_match_cache``
memo.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


class _Automaton:
    """Minimal Aho–Corasick: patterns → node ids, BFS failure links."""

    def __init__(self, patterns: dict[str, set[int]]) -> None:
        # Node storage: children maps, failure links, output doc-index sets.
        self._children: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._out: list[set[int]] = [set()]

        for pattern, doc_indices in patterns.items():
            node = 0
            for ch in pattern:
                nxt = self._children[node].get(ch)
                if nxt is None:
                    self._children.append({})
                    self._fail.append(0)
                    self._out.append(set())
                    nxt = len(self._children) - 1
                    self._children[node][ch] = nxt
                node = nxt
            self._out[node] |= doc_indices

        # BFS to set failure links and merge outputs along them.
        queue: deque[int] = deque(self._children[0].values())
        while queue:
            node = queue.popleft()
            for ch, child in self._children[node].items():
                queue.append(child)
                fail = self._fail[node]
                while fail and ch not in self._children[fail]:
                    fail = self._fail[fail]
                self._fail[child] = self._children[fail].get(ch, 0)
                if self._fail[child] == child:  # root self-loop guard
                    self._fail[child] = 0
                self._out[child] |= self._out[self._fail[child]]

    def search(self, text: str) -> set[int]:
        """Return the union of doc indices whose pattern occurs in ``text``."""
        matched: set[int] = set()
        node = 0
        for ch in text:
            while node and ch not in self._children[node]:
                node = self._fail[node]
            node = self._children[node].get(ch, 0)
            if self._out[node]:
                matched |= self._out[node]
        return matched


class DocMatchIndex:
    """Corpus-snapshot-keyed index over document match fields."""

    def __init__(
        self,
        snapshot: dict[str, tuple[str, str, str]],
        stems: dict[str, set[int]],
        automaton: _Automaton,
        doc_ids: list[str],
    ) -> None:
        self.snapshot = snapshot
        self._stems = stems
        self._automaton = automaton
        self._doc_ids = doc_ids

    def match(self, query_l: str, tokens: set[str]) -> list[str]:
        """Doc ids matching the query, in corpus (insertion) order."""
        matched = self._automaton.search(query_l)
        for token in tokens:
            matched |= self._stems.get(token, set())
        return [doc_id for i, doc_id in enumerate(self._doc_ids) if i in matched]


_index: DocMatchIndex | None = None


def get_doc_match_index(
    indexed_items: dict[str, Any],
    field_resolver: Any,
) -> DocMatchIndex:
    """Return the cached index, rebuilding when the corpus snapshot changed.

    ``field_resolver(doc_id, metadata)`` must return the memoized lowered
    ``(title, filename, relative_path, stems)`` tuple — the same fields the
    linear scan compared, so match results are identical by construction.
    """
    global _index

    snapshot: dict[str, tuple[str, str, str]] = {}
    for doc_id, meta in indexed_items.items():
        md = meta.get("metadata") or {}
        snapshot[doc_id] = (
            str(md.get("title") or ""),
            str(md.get("filename") or ""),
            str(md.get("relative_path") or ""),
        )

    if _index is not None and _index.snapshot == snapshot:
        return _index

    patterns: dict[str, set[int]] = {}
    stem_map: dict[str, set[int]] = {}
    doc_ids: list[str] = []
    for i, (doc_id, meta) in enumerate(indexed_items.items()):
        doc_ids.append(doc_id)
        md = meta.get("metadata") or {}
        title, filename, rel, stems = field_resolver(doc_id, md)
        for field in (title, filename, rel):
            if field:
                patterns.setdefault(field, set()).add(i)
        for stem in stems:
            stem_map.setdefault(stem, set()).add(i)

    _index = DocMatchIndex(snapshot, stem_map, _Automaton(patterns), doc_ids)
    logger.debug(
        "doc_match_index_rebuilt docs=%d patterns=%d", len(doc_ids), len(patterns)
    )
    return _index


__all__ = ["DocMatchIndex", "get_doc_match_index"]
