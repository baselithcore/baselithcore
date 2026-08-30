"""Prime an engineered loop with lessons from past campaigns.

An engineered loop's :class:`~core.loops.lessons.LessonLog` makes attempt
six smarter than attempt one — but attempt one of every campaign still
starts blind, re-discovering failure modes the wiki layer already recorded.
:func:`prime_lessons` fixes that first-attempt blindness: it ranks the
pattern store against the goal with BM25 (over ``title + summary`` — no
embeddings needed) and renders the top hits as a compact, bounded context
block. Nothing relevant means an empty string, never filler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from core.memory.hybrid_search import BM25Index
from core.skill_evolution.types import Pattern, PatternKind, PatternStatus

if TYPE_CHECKING:
    from core.skill_evolution.store import PatternStore

__all__ = ["MAX_PRIMER_CHARS", "prime_lessons"]

#: Hard cap on the rendered primer block (chars).
MAX_PRIMER_CHARS: Final[int] = 600

#: Per-bullet caps keeping any single pattern from eating the block.
_MAX_TITLE_CHARS: Final[int] = 80
_MAX_SUMMARY_CHARS: Final[int] = 160

#: How many patterns per kind are pulled from the store for ranking.
_SCAN_LIMIT: Final[int] = 200

_HEADER: Final[str] = "Lessons from past campaigns:"

_KIND_LABELS: Final[dict[PatternKind, str]] = {
    PatternKind.FAILURE_MODE: "failure",
    PatternKind.STRATEGY: "strategy",
}


def _one_line(text: str, max_chars: int) -> str:
    """Collapse whitespace to one line and truncate with an ellipsis."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1].rstrip() + "…"


def _bullet(pattern: Pattern) -> str:
    label = _KIND_LABELS.get(pattern.kind, pattern.kind.value)
    title = _one_line(pattern.title, _MAX_TITLE_CHARS)
    summary = _one_line(pattern.summary, _MAX_SUMMARY_CHARS)
    return f"- [{label}] {title}: {summary}"


async def prime_lessons(
    goal: str,
    store: PatternStore,
    *,
    k: int = 3,
    kinds: tuple[PatternKind, ...] = (
        PatternKind.FAILURE_MODE,
        PatternKind.STRATEGY,
    ),
) -> str:
    """Render the store's most goal-relevant patterns as a primer block.

    Args:
        goal: The campaign goal to rank patterns against.
        store: Wiki pattern store to draw lessons from.
        k: Maximum number of lessons rendered (>= 1).
        kinds: Pattern kinds considered; ``RETIRED`` patterns are always
            excluded regardless of kind.

    Returns:
        A "Lessons from past campaigns" block of at most ``k`` bullets and
        :data:`MAX_PRIMER_CHARS` characters — or ``""`` when the store is
        empty or no pattern scores above zero for the goal.

    Raises:
        ValueError: If ``k`` < 1.
    """
    if k < 1:
        raise ValueError("k must be >= 1")

    by_id: dict[str, Pattern] = {}
    for kind in kinds:
        for pattern in await store.list_patterns(kind=kind, limit=_SCAN_LIMIT):
            if pattern.status is not PatternStatus.RETIRED:
                by_id[pattern.id] = pattern
    if not by_id:
        return ""

    index = BM25Index()
    index.index({pid: f"{p.title}\n{p.summary}" for pid, p in by_id.items()})
    hits = index.search(goal, top_k=k)
    if not hits:
        return ""

    block = _HEADER
    for hit in hits:
        bullet = _bullet(by_id[hit.doc_id])
        if len(block) + 1 + len(bullet) > MAX_PRIMER_CHARS:
            break
        block = f"{block}\n{bullet}"
    if block == _HEADER:
        return ""
    return block
