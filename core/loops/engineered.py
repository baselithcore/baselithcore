"""The engineered loop: the system owns the iteration, not the human.

Re-prompting an agent after every failure makes the human the trigger, the
verifier, the memory and the escalation path. This module encodes those four
jobs so the loop runs them itself:

* the **verifier** decides whether the goal is met — the model never
  declares victory on its own word;
* the **stall guard** detects futility (same failure fingerprint N times)
  before the budget burns;
* the **lesson log** compacts each failure into one feed-forward line so
  attempt six is smarter than attempt one;
* the **escalation hook** hands a resumable state to a human when the loop
  loses.

The loop is deliberately transport-agnostic: *act* and *verify* are plain
async callables, so the same primitive drives a code-fix campaign, a data
backfill, or a document-refinement run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from core.loops.lessons import Lesson, LessonLog
from core.loops.stall import StallGuard
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["AttemptContext", "EngineeredLoop", "LoopOutcome", "LoopStatus"]

LoopStatus = Literal["success", "stalled", "exhausted", "budget_exceeded", "failed"]


@dataclass(frozen=True)
class AttemptContext:
    """Everything the actor is given for one attempt.

    Attributes:
        goal: The termination condition, verbatim.
        attempt: 1-based attempt number.
        lessons: Rendered feed-forward lessons ("" on the first attempt).
        last_evidence: Verifier output of the previous attempt ("" on the
            first attempt).
        last_fingerprint: Failure fingerprint of the previous attempt.
    """

    goal: str
    attempt: int
    lessons: str = ""
    last_evidence: str = ""
    last_fingerprint: str | None = None


@dataclass(frozen=True)
class LoopOutcome:
    """Terminal result of an :class:`EngineeredLoop` run."""

    status: LoopStatus
    goal: str
    attempts: int
    evidence: str = ""
    fingerprint: str | None = None
    reason: str | None = None
    lessons: list[Lesson] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """True when the verifier confirmed the termination condition."""
        return self.status == "success"

    def to_state(self) -> dict[str, Any]:
        """Serialize the outcome as a resumable state hand-off for a human.

        Returns:
            A JSON-serializable dict — what the escalation path needs to
            pick the campaign up without replaying it.
        """
        return {
            "status": self.status,
            "goal": self.goal,
            "attempts": self.attempts,
            "reason": self.reason,
            "fingerprint": self.fingerprint,
            "evidence": self.evidence,
            "lessons": [lesson.render() for lesson in self.lessons],
        }


#: Produce one attempt at the goal. The return value is ignored — the
#: verifier, not the actor, decides whether the work is done.
Actor = Callable[[AttemptContext], Awaitable[Any]]
#: Machine-checkable termination condition: ``(done, evidence)``.
Verifier = Callable[[], Awaitable[tuple[bool, str]]]
#: Called once with the terminal outcome when the loop does not succeed.
EscalationHook = Callable[[LoopOutcome], Awaitable[None]]


class EngineeredLoop:
    """Run *act* until *verify* passes, the loop stalls, or the budget ends.

    Args:
        act: Async callable invoked once per attempt with an
            :class:`AttemptContext`.
        verify: Async callable returning ``(done, evidence)``. It must be
            machine-checkable — if deciding "done" needs human judgment,
            fix the goal before building the loop.
        max_attempts: Hard cap on attempts (>= 1).
        stall_threshold: Consecutive identical failure fingerprints that
            count as futility. ``None`` disables stall detection.
        max_lessons: How many compacted lessons to feed forward.
        escalate: Optional hook invoked with the terminal outcome on any
            non-success status.
        budget: Optional ``core.orchestration.limits.LoopBudget``. Each
            attempt calls ``tick()``; exceeding the cap ends the run with
            ``budget_exceeded`` rather than raising.

    Example::

        async def verify() -> tuple[bool, str]:
            proc = await run("pytest -x -q")
            return proc.returncode == 0, proc.output

        loop = EngineeredLoop(act=patch_repo, verify=verify, max_attempts=6)
        outcome = await loop.run("pytest green, ruff clean")
    """

    def __init__(
        self,
        act: Actor,
        verify: Verifier,
        *,
        max_attempts: int = 6,
        stall_threshold: int | None = 3,
        max_lessons: int = 10,
        escalate: EscalationHook | None = None,
        budget: Any | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._act = act
        self._verify = verify
        self._max_attempts = max_attempts
        self._escalate = escalate
        self._budget = budget
        self._guard = (
            StallGuard(threshold=stall_threshold) if stall_threshold is not None else None
        )
        self._lessons = LessonLog(max_lessons=max_lessons)

    async def run(self, goal: str) -> LoopOutcome:
        """Drive the loop until the goal is verified or the loop gives up.

        Args:
            goal: The termination condition, stated so a machine can check it.

        Returns:
            The terminal :class:`LoopOutcome`. Never raises on ordinary
            failure — losing is a documented outcome, not an exception.
        """
        from core.orchestration.limits import BudgetExceededError

        last_evidence = ""
        last_fingerprint: str | None = None
        attempt = 0

        for attempt in range(1, self._max_attempts + 1):
            if self._budget is not None:
                try:
                    self._budget.tick()
                except BudgetExceededError as exc:
                    return await self._finish(
                        "budget_exceeded",
                        goal,
                        attempt - 1,
                        last_evidence,
                        last_fingerprint,
                        str(exc),
                    )

            context = AttemptContext(
                goal=goal,
                attempt=attempt,
                lessons=self._lessons.render(),
                last_evidence=last_evidence,
                last_fingerprint=last_fingerprint,
            )
            try:
                await self._act(context)
            except Exception as exc:  # a failed attempt is data, not a crash
                logger.warning("Loop attempt %d raised: %s", attempt, exc)
                last_evidence = f"Error: actor raised {type(exc).__name__}: {exc}"
            else:
                done, evidence = await self._verify()
                if done:
                    logger.info("Loop goal verified after %d attempt(s)", attempt)
                    return LoopOutcome(
                        status="success",
                        goal=goal,
                        attempts=attempt,
                        evidence=evidence,
                        lessons=self._lessons.lessons,
                    )
                last_evidence = evidence

            fingerprint = self._record(attempt, last_evidence)
            last_fingerprint = fingerprint
            if self._guard is not None:
                verdict = self._guard.record(last_evidence)
                if verdict.stalled:
                    return await self._finish(
                        "stalled",
                        goal,
                        attempt,
                        last_evidence,
                        verdict.fingerprint,
                        verdict.reason,
                    )

        return await self._finish(
            "exhausted",
            goal,
            attempt,
            last_evidence,
            last_fingerprint,
            f"max attempts exhausted ({self._max_attempts})",
        )

    def _record(self, attempt: int, evidence: str) -> str:
        """Compact a failed attempt into a lesson; return its fingerprint."""
        from core.loops.fingerprint import failure_fingerprint

        fingerprint = failure_fingerprint(evidence)
        self._lessons.record(attempt, evidence, fingerprint)
        return fingerprint

    async def _finish(
        self,
        status: LoopStatus,
        goal: str,
        attempts: int,
        evidence: str,
        fingerprint: str | None,
        reason: str | None,
    ) -> LoopOutcome:
        """Build the terminal outcome and run the escalation hook."""
        outcome = LoopOutcome(
            status=status,
            goal=goal,
            attempts=attempts,
            evidence=evidence,
            fingerprint=fingerprint,
            reason=reason,
            lessons=self._lessons.lessons,
        )
        logger.warning("Loop ended with status=%s: %s", status, reason)
        if self._escalate is not None:
            try:
                await self._escalate(outcome)
            except Exception as exc:  # escalation must never mask the outcome
                logger.error("Loop escalation hook failed: %s", exc)
        return outcome
