"""The verified loop: propose code, run the tests in the sandbox, learn, repeat.

A thin layer over ``baselith.core.loops.EngineeredLoop``: the actor asks the model for a candidate (with
the lessons of the failed attempts and the last test run), the verifier runs the candidate's
tests in the sandbox, and the loop's stall guard ends a run that keeps hitting the same failure.
The model never declares success: a green test run does. The best candidate seen (most tests
passing) is kept, so a lost run still returns the closest attempt.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from baselith.core.loops import AttemptContext, EngineeredLoop

from .harness import TestReport


@dataclass(frozen=True, slots=True)
class Candidate:
    """One attempt: the code, the tests it is held to, and what the model said about it."""

    code: str
    tests: str
    explanation: str = ""
    changes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Verified:
    """How a verified loop ended."""

    status: str
    attempts: int
    best: Candidate | None
    report: TestReport | None
    seconds: float

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


Propose = Callable[
    [AttemptContext, Candidate | None, TestReport | None], Awaitable[Candidate]
]
RunTests = Callable[[Candidate], Awaitable[TestReport]]


def _score(report: TestReport | None) -> tuple[int, int]:
    if report is None or report.problem or report.collection_error:
        return (0, 0)
    return (1 if report.green else 0, report.passed)


async def verified_loop(
    goal: str, propose: Propose, run_tests: RunTests, *, max_attempts: int
) -> Verified:
    """Run ``propose`` and ``run_tests`` until the tests are green or the loop gives up.

    Args:
        goal: The termination condition in words (logged, and seen by the loop's lessons).
        propose: Builds the next candidate from the attempt context, the last candidate and
            its report. An exception is a failed attempt (its type is the evidence), not a crash.
        run_tests: Runs one candidate's tests in the sandbox.
        max_attempts: Hard cap on attempts.
    """
    last: Candidate | None = None
    last_report: TestReport | None = None
    best: Candidate | None = None
    best_report: TestReport | None = None
    seconds = 0.0
    problem = ""

    async def act(ctx: AttemptContext) -> None:
        nonlocal last, problem
        problem = ""
        try:
            last = await propose(ctx, last, last_report)
        except Exception as exc:
            problem = f"the model gave no usable answer ({type(exc).__name__})"

    async def verify() -> tuple[bool, str]:
        nonlocal last_report, best, best_report, seconds
        if problem or last is None:
            return False, problem or "no candidate yet"
        report = await run_tests(last)
        last_report = report
        seconds += report.seconds
        if best is None or _score(report) > _score(best_report):
            best, best_report = last, report
        return report.green, report.evidence()

    loop = EngineeredLoop(act, verify, max_attempts=max_attempts, stall_threshold=3)
    outcome = await loop.run(goal)
    return Verified(outcome.status, outcome.attempts, best, best_report, seconds)


__all__ = ["Candidate", "Verified", "verified_loop"]
