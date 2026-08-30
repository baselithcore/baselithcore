"""Bounded reflective mutation: small edits, enforced by code.

An unconstrained "improve this" rewrite loses the parent's accumulated
structure and makes lineage analysis meaningless. The mutator asks the
model to *reflect* on the parent's concrete failures and return a complete
revision changing at most N lines — and then verifies that bound with a
real diff. The prompt requests the limit; the code enforces it. Oversized,
empty, or identical outputs are rejected, never trimmed.
"""

from __future__ import annotations

import difflib
from collections.abc import Awaitable, Callable
from typing import Final

from core.observability.logging import get_logger
from core.optimization.evolution.types import Candidate

logger = get_logger(__name__)

__all__ = ["ReflectiveMutator"]

#: Cap on failure notes folded into the reflection prompt.
_MAX_FAILURE_NOTES: Final[int] = 5
#: Cap on characters kept per failure note.
_MAX_NOTE_CHARS: Final[int] = 200

_PROMPT_TEMPLATE: Final[str] = (
    "You are refining a text artifact through one small, targeted edit.\n\n"
    "CURRENT VERSION:\n{content}\n\n"
    "OBSERVED FAILURES (address these):\n{failures}\n\n"
    "Return the COMPLETE revised text, changing AT MOST {max_lines} lines "
    "compared to the current version. Keep everything else verbatim. "
    "Output only the revised text, with no commentary or code fences."
)


def _changed_lines(parent: str, child: str) -> int:
    """Count changed lines between two texts (replace counts once)."""
    matcher = difflib.SequenceMatcher(
        a=parent.splitlines(), b=child.splitlines(), autojunk=False
    )
    changed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            changed += max(i2 - i1, j2 - j1)
    return changed


def _compact_failures(failures: list[str]) -> str:
    """Fold failure notes into a bounded bullet list for the prompt."""
    notes = [
        f"- {note[:_MAX_NOTE_CHARS]}"
        for note in failures[:_MAX_FAILURE_NOTES]
        if note.strip()
    ]
    return "\n".join(notes) if notes else "- (no failure details available)"


class ReflectiveMutator:
    """Produce bounded child revisions of a candidate via an LLM callable.

    Args:
        generate: Async text generator ``prompt -> completion`` (typically
            an LLM-service adapter; tests pass a fake).
        max_changed_lines: Maximum lines the child may differ from the
            parent by, verified with :mod:`difflib` — a replaced line
            counts once, insertions and deletions count per line.
    """

    def __init__(
        self,
        generate: Callable[[str], Awaitable[str]],
        *,
        max_changed_lines: int = 20,
    ) -> None:
        if max_changed_lines < 1:
            raise ValueError("max_changed_lines must be >= 1")
        self._generate = generate
        self._max_changed_lines = max_changed_lines

    async def mutate(self, parent: Candidate, failures: list[str]) -> str | None:
        """Generate one bounded revision of ``parent``.

        Args:
            parent: The candidate to revise.
            failures: Compacted notes on where the parent scored worst.

        Returns:
            The complete child text, or ``None`` when the generation is
            empty, identical to the parent, or exceeds the changed-line
            bound (each rejection is logged).
        """
        prompt = _PROMPT_TEMPLATE.format(
            content=parent.content,
            failures=_compact_failures(failures),
            max_lines=self._max_changed_lines,
        )
        child = await self._generate(prompt)
        if not child or not child.strip():
            logger.warning(
                "reflective_mutation_rejected parent=%s reason=empty", parent.id
            )
            return None
        if child == parent.content:
            logger.warning(
                "reflective_mutation_rejected parent=%s reason=identical",
                parent.id,
            )
            return None
        changed = _changed_lines(parent.content, child)
        if changed > self._max_changed_lines:
            logger.warning(
                "reflective_mutation_rejected parent=%s reason=over_bound "
                "changed=%d max=%d",
                parent.id,
                changed,
                self._max_changed_lines,
            )
            return None
        return child
