"""Distill wiki STRATEGY patterns into the few-shot example library.

The pattern store accumulates *what worked* (``STRATEGY`` patterns distilled
from high-scoring evaluations), but that knowledge only pays off when it
reaches a prompt. This module closes the loop: promoted strategies — and
candidates observed often enough to trust — become
:class:`~core.personas.few_shot.FewShotExample` entries, so the persona
manager splices them into system prompts like any curated example.

Idempotency: each example carries a ``pattern:<fingerprint>`` tag, so
re-syncing an already-converted pattern is a no-op. The dedup key lives in
the example's own ``tags`` metadata (the library model supports tags), which
makes the guarantee hold across separate calls sharing one library — no
external seen-set to thread around.
"""

from __future__ import annotations

from typing import Final

from core.personas.few_shot import FewShotExample, FewShotLibrary
from core.skill_evolution.store import PatternStore
from core.skill_evolution.types import Pattern, PatternKind, PatternStatus

__all__ = ["patterns_to_few_shot", "sync_strategies_to_library"]

#: Tag prefix carrying the source pattern's fingerprint (the dedup key).
FINGERPRINT_TAG_PREFIX: Final[str] = "pattern:"

#: Tag marking examples produced by this module (vs. curated ones).
_ORIGIN_TAG: Final[str] = "skill-evolution"

#: How many STRATEGY patterns one sync pulls from the store.
_SYNC_LIST_LIMIT: Final[int] = 200


def _eligible(pattern: Pattern, min_occurrences: int) -> bool:
    """A STRATEGY worth teaching: promoted, or a well-observed candidate."""
    if pattern.kind is not PatternKind.STRATEGY:
        return False
    if not pattern.summary.strip() or not pattern.title.strip():
        return False
    if pattern.status is PatternStatus.PROMOTED:
        return True
    return (
        pattern.status is PatternStatus.CANDIDATE
        and pattern.occurrences >= min_occurrences
    )


def _known_fingerprints(library: FewShotLibrary, task: str) -> set[str]:
    """Fingerprints already registered under ``task`` (via example tags)."""
    return {
        tag.removeprefix(FINGERPRINT_TAG_PREFIX)
        for example in library.examples.get(task, [])
        for tag in example.tags
        if tag.startswith(FINGERPRINT_TAG_PREFIX)
    }


def patterns_to_few_shot(
    patterns: list[Pattern],
    library: FewShotLibrary,
    *,
    task: str,
    min_occurrences: int = 3,
) -> int:
    """Register eligible STRATEGY patterns as few-shot examples.

    Eligible means ``PROMOTED``, or ``CANDIDATE`` with at least
    ``min_occurrences`` observations; ``RETIRED`` and non-strategy patterns
    are skipped. Each example maps the pattern title to its distilled
    summary (``input`` -> ``output``) with provenance in the rationale.

    Args:
        patterns: Candidate patterns (any kind/status — filtered here).
        library: Destination library; examples land under ``task``.
        task: Task-type bucket to register the examples under.
        min_occurrences: Observation floor for ``CANDIDATE`` patterns.

    Returns:
        Number of examples actually added. Idempotent per pattern: a
        fingerprint already present in the task bucket (tracked via the
        ``pattern:<fingerprint>`` example tag) is not added again.
    """
    seen = _known_fingerprints(library, task)
    added = 0
    for pattern in patterns:
        if not _eligible(pattern, min_occurrences) or pattern.fingerprint in seen:
            continue
        library.add(
            task,
            FewShotExample(
                input=pattern.title,
                output=pattern.summary,
                rationale=(
                    f"Distilled strategy pattern "
                    f"({pattern.occurrences} observation(s), "
                    f"status {pattern.status.value})."
                ),
                tags=(
                    _ORIGIN_TAG,
                    PatternKind.STRATEGY.value,
                    f"{FINGERPRINT_TAG_PREFIX}{pattern.fingerprint}",
                ),
            ),
        )
        seen.add(pattern.fingerprint)
        added += 1
    return added


async def sync_strategies_to_library(
    store: PatternStore,
    library: FewShotLibrary,
    *,
    task: str,
    min_occurrences: int = 3,
) -> int:
    """Pull STRATEGY patterns from the store into the few-shot library.

    Args:
        store: Wiki pattern store to read from.
        library: Destination few-shot library.
        task: Task-type bucket to register the examples under.
        min_occurrences: Observation floor for ``CANDIDATE`` patterns.

    Returns:
        Number of examples added this sync (0 when everything eligible is
        already registered — safe to call on a schedule).
    """
    patterns = await store.list_patterns(
        kind=PatternKind.STRATEGY, limit=_SYNC_LIST_LIMIT
    )
    return patterns_to_few_shot(
        patterns, library, task=task, min_occurrences=min_occurrences
    )
