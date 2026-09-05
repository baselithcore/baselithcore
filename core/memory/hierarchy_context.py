"""Context assembly for hierarchical memory.

``get_context`` renders the three tiers into a token-budgeted markdown block
for LLM injection. Split out of ``hierarchy.py`` to respect the module size
cap; mixed into :class:`~core.memory.hierarchy.HierarchicalMemory`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from .hybrid_search import BM25Index

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .types import MemoryItem

#: Items rendered in the ``## Background`` (MTM) section.
_BACKGROUND_ITEMS: Final[int] = 5
#: Summaries rendered in the ``## Long-term Knowledge`` (LTM) section.
_LONG_TERM_ITEMS: Final[int] = 3
#: ~20 tokens of headroom before opening a new section (heading + a line).
_SECTION_MIN: Final[int] = 20


def _rank_for_query(
    items: list[MemoryItem], query: str, top_n: int
) -> list[MemoryItem]:
    """Query-gated selection: BM25 keyword hits (best first), then the most
    recent remaining items backfill up to ``top_n``.

    A static keyword lookup — pure CPU, sub-millisecond on tier-sized
    corpora — so it runs inside the synchronous ``get_context`` with no
    embedder round trip: the same cheap-lookup-before-expensive-computation
    split the recall path uses for its BM25 rescue pass.
    """
    if not items or top_n <= 0:
        return []
    index = BM25Index()
    index.index({str(pos): item.content for pos, item in enumerate(items)})
    chosen = [items[int(hit.doc_id)] for hit in index.search(query, top_k=top_n)]
    chosen_ids = {id(item) for item in chosen}
    for item in reversed(items):  # most recent first
        if len(chosen) >= top_n:
            break
        if id(item) not in chosen_ids:
            chosen.append(item)
            chosen_ids.add(id(item))
    return chosen


class HierarchyContextMixin:
    """Token-budgeted context rendering over the STM/MTM/LTM tiers."""

    # Provided by HierarchicalMemory (annotations mirror HierarchySearchMixin).
    _stm: list[MemoryItem]
    _mtm: list[MemoryItem]
    _ltm: Iterable[MemoryItem]  # deque(maxlen=...) in HierarchicalMemory

    def _background_items(self, query: str | None) -> list[MemoryItem]:
        if query:
            return _rank_for_query(self._mtm, query, _BACKGROUND_ITEMS)
        return list(reversed(self._mtm[-_BACKGROUND_ITEMS:]))  # most recent first

    def _long_term_items(self, query: str | None) -> list[MemoryItem]:
        summaries = [i for i in self._ltm if i.metadata.get("is_summary")]
        if query:
            return _rank_for_query(summaries, query, _LONG_TERM_ITEMS)
        return summaries[-_LONG_TERM_ITEMS:]

    def get_context(self, max_tokens: int = 2000, query: str | None = None) -> str:
        """
        Assemble a formatted context string for LLM injection.

        STM is always rendered most-recent first: it *is* the immediate
        context. The ``## Background`` (MTM) and ``## Long-term Knowledge``
        (LTM summaries) sections are recency-only by default; pass ``query``
        to gate them by relevance instead — keyword hits for the current
        request lead each section and recent items only backfill what is
        left, so an old-but-pertinent note is not crowded out by newer
        unrelated ones (and unrelated recent ones stop spending the budget).

        Budgets by **tokens** (via ``core.utils.tokens.estimate_tokens``:
        tiktoken when available, a content-aware heuristic otherwise) — not raw
        character length — so the returned context actually fits the model
        window the caller sized ``max_tokens`` against.

        Args:
            max_tokens: Token budget for the total returned context.
            query: Optional current request; gates the Background and
                Long-term sections by keyword relevance (BM25).

        Returns:
            str: A formatted markdown block containing structured
                 context sections.
        """
        from core.utils.tokens import estimate_tokens

        parts: list[str] = []
        remaining = max_tokens

        def render(heading: str, items: list[MemoryItem]) -> None:
            nonlocal remaining
            parts.append(heading)
            for item in items:
                line = f"- {item.content}\n"
                cost = estimate_tokens(line)
                if cost > remaining:
                    break
                parts.append(line)
                remaining -= cost

        if self._stm:
            render("## Recent Context\n", list(reversed(self._stm)))
        if self._mtm and remaining > _SECTION_MIN:
            render("\n## Background\n", self._background_items(query))
        if self._ltm and remaining > _SECTION_MIN:
            long_term = self._long_term_items(query)
            if long_term:
                render("\n## Long-term Knowledge\n", long_term)
        return "".join(parts)


__all__ = ["HierarchyContextMixin"]
