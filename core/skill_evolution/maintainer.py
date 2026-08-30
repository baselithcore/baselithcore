"""Wiki maintainer: distills run outcomes into persistent patterns.

Two ingestion paths feed the wiki layer:

- Evaluation events (``EVALUATION_COMPLETED`` payloads): low scores become
  ``FAILURE_MODE`` patterns fingerprinted on the verifier feedback, high
  scores become ``STRATEGY`` patterns.
- Engineered-loop lessons (:class:`core.loops.lessons.LessonLog`), which
  today die with the loop — :meth:`WikiMaintainer.persist_lessons` gives
  them a durable home.

An optional ``rca`` callable (an LLM root-cause analysis) can replace the
deterministic summary for failures; it is fail-soft by contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from core.loops.fingerprint import failure_fingerprint
from core.loops.lessons import Lesson, compact_evidence
from core.observability.logging import get_logger
from core.skill_evolution.store import PatternStore
from core.skill_evolution.types import (
    EvidenceRef,
    Pattern,
    PatternKind,
)

logger = get_logger(__name__)

__all__ = ["WikiMaintainer", "safe_score", "safe_run_id"]

#: Characters of a successful response hashed/kept for strategy patterns.
_STRATEGY_HEAD_CHARS = 400

#: Concurrent RCA LLM calls allowed (the judge limiter does not cover them).
_RCA_MAX_CONCURRENCY = 2


def safe_score(data: dict[str, Any]) -> float | None:
    """Extract a numeric score from an event payload, or None.

    None (missing key, ``None`` value, non-numeric) means "unscored": the
    caller must skip rather than fabricate a 0.0 failure.
    """
    value = data.get("score")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def safe_run_id(data: dict[str, Any]) -> str | None:
    """Extract the run id from an event payload; only absence maps to None.

    Falsy-but-present ids are preserved (``0`` becomes ``"0"``) so integer
    run ids attribute correctly; only ``None`` and ``""`` count as absent.
    """
    value = data.get("run_id")
    if value is None:
        return None
    text = str(value)
    return text if text else None


class WikiMaintainer:
    """Distill evaluations and lessons into the pattern store."""

    def __init__(
        self,
        store: PatternStore,
        *,
        failure_threshold: float = 0.4,
        success_threshold: float = 0.9,
        rca: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        """Initialize the maintainer.

        Args:
            store: Wiki pattern store (any :class:`PatternStore` backend).
            failure_threshold: Scores strictly below become failure patterns.
            success_threshold: Scores strictly above become strategy patterns.
            rca: Optional async root-cause analyzer (evidence -> summary).
                Errors fall back to the deterministic compact summary.
        """
        self._store = store
        self._failure_threshold = failure_threshold
        self._success_threshold = success_threshold
        self._rca = rca
        self._rca_semaphore = asyncio.Semaphore(_RCA_MAX_CONCURRENCY)

    async def distill_evaluation(self, data: dict[str, Any]) -> Pattern | None:
        """Turn one ``EVALUATION_COMPLETED`` payload into a pattern upsert.

        Args:
            data: Event payload (``score``, ``intent``, ``feedback``,
                ``response``, optional ``run_id``).

        Returns:
            The upserted (possibly merged) pattern, or None when the payload
            is unscored, the score falls in the neutral band, or the payload
            carries no evidence.
        """
        score = safe_score(data)
        if score is None:
            return None
        intent = str(data.get("intent") or "unknown_intent")
        evidence_ref = EvidenceRef(run_id=safe_run_id(data), score=score)

        if score < self._failure_threshold:
            feedback = str(data.get("feedback") or "")
            if not feedback.strip():
                return None
            pattern = Pattern(
                fingerprint=failure_fingerprint(feedback),
                kind=PatternKind.FAILURE_MODE,
                title=f"Failure: {intent}",
                summary=await self._summarize_failure(feedback),
                evidence=[evidence_ref],
            )
            return await self._store.upsert(pattern)

        if score > self._success_threshold:
            response = str(data.get("response") or "")
            if not response.strip():
                return None
            head = response[:_STRATEGY_HEAD_CHARS]
            pattern = Pattern(
                fingerprint=failure_fingerprint(f"{intent}\n{head}"),
                kind=PatternKind.STRATEGY,
                title=f"Strategy: {intent}",
                summary=head,
                evidence=[evidence_ref],
            )
            return await self._store.upsert(pattern)

        return None

    async def persist_lessons(
        self, lessons: Iterable[Lesson], *, run_id: str | None = None
    ) -> list[Pattern]:
        """Persist engineered-loop lessons as failure patterns.

        Reuses each lesson's fingerprint and summary so repeated campaigns
        hitting the same failure merge instead of accumulating duplicates.

        Args:
            lessons: Lessons from a :class:`core.loops.lessons.LessonLog`.
            run_id: Run identifier recorded as evidence, when known.

        Returns:
            The upserted patterns, in lesson order.
        """
        stored: list[Pattern] = []
        for lesson in lessons:
            pattern = Pattern(
                fingerprint=lesson.fingerprint,
                kind=PatternKind.FAILURE_MODE,
                title=f"Loop failure [{lesson.fingerprint}]",
                summary=lesson.summary,
                evidence=[EvidenceRef(run_id=run_id, note=f"attempt {lesson.attempt}")],
            )
            stored.append(await self._store.upsert(pattern))
        return stored

    async def _summarize_failure(self, feedback: str) -> str:
        """RCA summary when available, deterministic compaction otherwise.

        The RCA call runs under a small semaphore: it sits outside the
        evaluation judges' concurrency limiter, so a failure burst must not
        fan out one unbounded LLM call per event.
        """
        if self._rca is not None:
            try:
                async with self._rca_semaphore:
                    summary = await self._rca(feedback)
                if summary.strip():
                    return summary.strip()
            except Exception as exc:
                logger.warning("RCA summarizer failed, using compact evidence: %s", exc)
        return compact_evidence(feedback)
