"""Lesson compaction for engineered loops.

A loop that feeds raw transcripts forward overflows the context window and
makes the agent re-derive what already went wrong. A loop that feeds
*lessons* forward — one structured line per failed attempt, what was tried
and how it failed — keeps attempt six smarter than attempt one instead of
merely longer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.loops.fingerprint import failure_lines

__all__ = ["Lesson", "LessonLog"]


@dataclass(frozen=True)
class Lesson:
    """One compacted failed attempt."""

    attempt: int
    fingerprint: str
    summary: str

    def render(self) -> str:
        """Render as a single feed-forward line."""
        return f"Attempt {self.attempt} failed [{self.fingerprint}]: {self.summary}"


def compact_evidence(evidence: str, *, max_chars: int = 240, max_lines: int = 3) -> str:
    """Reduce raw verifier output to a short, deterministic summary line.

    Args:
        evidence: Raw verifier output.
        max_chars: Hard cap on the returned string.
        max_lines: How many failure lines to keep before truncating.

    Returns:
        A single-line summary; ``"(no failure detail captured)"`` when the
        evidence carries no usable text.
    """
    lines = failure_lines(evidence)[:max_lines]
    if not lines:
        return "(no failure detail captured)"
    summary = " | ".join(lines)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"
    return summary


@dataclass
class LessonLog:
    """Accumulate compacted lessons and render them as loop context.

    Args:
        max_lessons: Keep only the most recent N lessons when rendering.
            Bounds the context the loop feeds forward on long campaigns.
    """

    max_lessons: int = 10
    _lessons: list[Lesson] = field(default_factory=list, init=False, repr=False)

    def __len__(self) -> int:
        return len(self._lessons)

    @property
    def lessons(self) -> list[Lesson]:
        """All recorded lessons, oldest first."""
        return list(self._lessons)

    def record(self, attempt: int, evidence: str, fingerprint: str) -> Lesson:
        """Compact one failed attempt into a lesson and store it.

        Args:
            attempt: 1-based attempt number.
            evidence: Raw verifier output for the failed attempt.
            fingerprint: Failure fingerprint of the attempt.

        Returns:
            The stored :class:`Lesson`.
        """
        lesson = Lesson(
            attempt=attempt, fingerprint=fingerprint, summary=compact_evidence(evidence)
        )
        self._lessons.append(lesson)
        return lesson

    def render(self) -> str:
        """Render the retained lessons as feed-forward context.

        Returns:
            One line per lesson (most recent ``max_lessons``), empty string
            when nothing has been recorded yet.
        """
        kept = self._lessons[-self.max_lessons :]
        if not kept:
            return ""
        header = "Previous attempts failed. Do not repeat these approaches:"
        return "\n".join([header, *(lesson.render() for lesson in kept)])

    def clear(self) -> None:
        """Drop every recorded lesson."""
        self._lessons.clear()
