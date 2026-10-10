"""The four verified tasks: fix, write, refactor and tests.

Each flow talks to the model only through ``kit.ask`` (typed answers) and to the sandbox only
through ``kit.run_tests``, so it is testable with a scripted model and a local runner. Tests are
written once and then frozen: later attempts may change only the code, except when the test file
itself cannot be collected. Success always means a green test run.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from core.loops import AttemptContext

from .harness import TestReport
from .loop import Candidate, Verified, verified_loop
from .prompts import fix_prompt, refactor_prompt, tests_prompt, write_prompt
from .schemas import CodeAnswer, CodeWithTests, TestsAnswer
from .types import CodingResult

M = TypeVar("M", bound=BaseModel)

#: Attempts at a characterization test file that passes on the code a refactor starts from.
CHARACTERIZATION_ATTEMPTS = 3


class Kit(Protocol):
    """What a flow needs from the agent."""

    language: str
    max_attempts: int

    async def ask(self, prompt: str, model: type[M]) -> M: ...

    async def run_tests(self, code: str, tests: str) -> TestReport: ...


def _needs_new_tests(report: TestReport | None) -> bool:
    return report is not None and report.collection_error


def _result(
    original: str, run: Verified, *, fallback: str, explanation: str
) -> CodingResult:
    best, report = run.best, run.report
    passed = report.passed if report is not None else 0
    total = report.total if report is not None else 0
    return CodingResult(
        success=run.succeeded,
        original_code=original,
        final_code=best.code if best is not None else fallback,
        iterations=run.attempts,
        error=None
        if run.succeeded
        else (report.evidence() if report else "no candidate"),
        explanation=(best.explanation if best is not None else "") or explanation,
        tests_passed=passed,
        tests_total=total,
        test_code=best.tests if best is not None else "",
        changes=list(best.changes) if best is not None else [],
        loop_status=run.status,
        sandbox_seconds=run.seconds,
    )


async def _code_or_both(
    kit: Kit, prompt: str, tests: str | None
) -> tuple[str, str, str, tuple[str, ...]]:
    """Code (and, without frozen ``tests``, new tests) for ``prompt``."""
    if tests is None:
        both = await kit.ask(prompt, CodeWithTests)
        return both.code, both.tests, both.explanation, tuple(both.changes)
    one = await kit.ask(prompt, CodeAnswer)
    return one.code, tests, one.explanation, tuple(one.changes)


async def fix(kit: Kit, code: str, error: str, context: str = "") -> CodingResult:
    """Fix ``code``: the first answer brings tests that reproduce the bug, then they are frozen."""
    frozen: list[str] = []

    async def propose(
        ctx: AttemptContext, last: Candidate | None, report: TestReport | None
    ) -> Candidate:
        if _needs_new_tests(report):
            frozen.clear()
        tests = frozen[0] if frozen else None
        prompt = fix_prompt(
            kit.language,
            code,
            error,
            context,
            current=last.code if last else None,
            tests=tests,
            lessons=ctx.lessons,
            evidence=ctx.last_evidence,
        )
        new_code, new_tests, why, changes = await _code_or_both(kit, prompt, tests)
        if not frozen:
            frozen.append(new_tests)
        return Candidate(new_code, new_tests, why, changes)

    run = await verified_loop(
        "every test passes on the fixed code",
        propose,
        lambda c: kit.run_tests(c.code, c.tests),
        max_attempts=kit.max_attempts,
    )
    result = _result(code, run, fallback=code, explanation="")
    if frozen:
        on_original = await kit.run_tests(code, frozen[0])
        result.sandbox_seconds += on_original.seconds
        result.tests_reproduce_bug = not on_original.green
    return result


async def write(
    kit: Kit, description: str, examples: list[str] | None = None
) -> CodingResult:
    """Write code for ``description``, held to the tests of the first answer."""
    frozen: list[str] = []

    async def propose(
        ctx: AttemptContext, last: Candidate | None, report: TestReport | None
    ) -> Candidate:
        if _needs_new_tests(report):
            frozen.clear()
        tests = frozen[0] if frozen else None
        prompt = write_prompt(
            kit.language,
            description,
            examples,
            current=last.code if last else None,
            tests=tests,
            lessons=ctx.lessons,
            evidence=ctx.last_evidence,
        )
        new_code, new_tests, why, changes = await _code_or_both(kit, prompt, tests)
        if not frozen:
            frozen.append(new_tests)
        return Candidate(new_code, new_tests, why, changes)

    run = await verified_loop(
        "the code meets the request and every test passes",
        propose,
        lambda c: kit.run_tests(c.code, c.tests),
        max_attempts=kit.max_attempts,
    )
    return _result("", run, fallback="", explanation="")


async def characterize(kit: Kit, code: str) -> tuple[str, TestReport | None, float]:
    """Tests that pass on ``code`` as it is now: ``("", report, seconds)`` when none could be made."""
    evidence = ""
    report: TestReport | None = None
    seconds = 0.0
    for _ in range(CHARACTERIZATION_ATTEMPTS):
        answer = await kit.ask(
            tests_prompt(kit.language, code, evidence=evidence), TestsAnswer
        )
        report = await kit.run_tests(code, answer.tests)
        seconds += report.seconds
        if report.green:
            return answer.tests, report, seconds
        evidence = report.evidence()
    return "", report, seconds


async def refactor(kit: Kit, code: str, goals: str = "") -> CodingResult:
    """Refactor ``code`` for ``goals`` while characterization tests stay green."""
    tests, base, base_seconds = await characterize(kit, code)
    if not tests:
        return CodingResult(
            success=False,
            original_code=code,
            final_code=code,
            error=(
                "could not write tests that pass on the current code, so a refactor could not "
                "be verified: " + (base.evidence() if base else "no test file")
            ),
            loop_status="failed",
            sandbox_seconds=base_seconds,
        )

    async def propose(
        ctx: AttemptContext, last: Candidate | None, report: TestReport | None
    ) -> Candidate:
        prompt = refactor_prompt(
            kit.language,
            code,
            goals,
            tests,
            current=last.code if last else None,
            lessons=ctx.lessons,
            evidence=ctx.last_evidence,
        )
        answer = await kit.ask(prompt, CodeAnswer)
        return Candidate(answer.code, tests, answer.explanation, tuple(answer.changes))

    run = await verified_loop(
        "the refactored code passes every characterization test",
        propose,
        lambda c: kit.run_tests(c.code, c.tests),
        max_attempts=kit.max_attempts,
    )
    result = _result(code, run, fallback=code, explanation="")
    result.sandbox_seconds += base_seconds
    return result


async def write_tests(kit: Kit, code: str) -> CodingResult:
    """Tests for ``code``, run once against it (rewritten only if they cannot be collected)."""
    answer = await kit.ask(tests_prompt(kit.language, code), TestsAnswer)
    report = await kit.run_tests(code, answer.tests)
    seconds = report.seconds
    if report.collection_error or report.problem:
        answer = await kit.ask(
            tests_prompt(kit.language, code, evidence=report.evidence()), TestsAnswer
        )
        report = await kit.run_tests(code, answer.tests)
        seconds += report.seconds
    usable = report.total > 0 and not report.collection_error and not report.problem
    failing = report.total - report.passed
    note = (
        f"{report.passed} of {report.total} tests pass on the code"
        + (f"; {failing} fail and may point at bugs" if failing else "")
        if usable
        else report.evidence()
    )
    return CodingResult(
        success=usable,
        original_code=code,
        final_code=answer.tests,
        iterations=1,
        error=None if usable else report.evidence(),
        explanation=f"{answer.explanation} {note}".strip(),
        tests_passed=report.passed,
        tests_total=report.total,
        test_code=answer.tests,
        loop_status="success" if usable else "failed",
        sandbox_seconds=seconds,
    )


__all__ = ["Kit", "characterize", "fix", "refactor", "write", "write_tests"]
