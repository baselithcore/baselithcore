"""The verified and unverified fix flows over a :class:`Workspace`."""

from __future__ import annotations

import difflib
from collections.abc import Awaitable, Callable
from typing import Any

from baselith.core.loops import AttemptContext
from baselith.plugins.coding_agent.harness import TestReport
from baselith.plugins.coding_agent.loop import Candidate, verified_loop
from baselith.plugins.coding_agent.schemas import WorkspaceCode, WorkspaceFix
from baselith.plugins.coding_agent.workspace import Workspace, WorkspaceResult
from baselith.plugins.coding_agent.workspace_prompts import workspace_fix_prompt

Ask = Callable[[str, Any], Awaitable[Any]]
RunTests = Callable[[str, str], Awaitable[TestReport]]

_NO_MODULE = "No module named '"


def unified(ws: Workspace, new: str) -> str:
    """The unified diff of the target, original to ``new``."""
    return "".join(
        difflib.unified_diff(
            ws.files[ws.target].splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{ws.target}",
            tofile=f"b/{ws.target}",
        )
    )


def missing_module(report: TestReport, ws: Workspace) -> str | None:
    """The top-level module a collection error could not import, when no workspace file is it."""
    if not report.collection_error:
        return None
    local = {p.rsplit("/", 1)[-1].removesuffix(".py") for p in ws.files}
    local |= {part for p in ws.files for part in p.split("/")[:-1]}
    for _name, text in report.failures:
        if _NO_MODULE in text:
            module = text.split(_NO_MODULE, 1)[1].split("'", 1)[0].split(".")[0]
            if module and module not in local:
                return module
    return None


async def unverified(
    ask: Ask, ws: Workspace, problem: str, language: str, reason: str
) -> WorkspaceResult:
    """One typed answer, nothing executed: a proposal the repository's CI has to judge."""
    prompt = workspace_fix_prompt(
        language, ws.target, ws.files, problem, ws.roots, ws.test_path
    )
    answer: WorkspaceFix = await ask(prompt, WorkspaceFix)
    original = ws.files[ws.target]
    changed = bool(answer.code.strip()) and answer.code != original
    return WorkspaceResult(
        success=changed,
        original_code=original,
        final_code=answer.code if changed else original,
        iterations=1,
        error=None if changed else "the answer did not change the file",
        explanation=answer.explanation,
        test_code=answer.tests,
        changes=list(answer.changes),
        loop_status="unverified",
        target=ws.target,
        test_path=ws.test_path,
        verified=False,
        unverified_reason=reason,
        diff=unified(ws, answer.code) if changed else "",
    )


async def verified(
    ask: Ask,
    run_tests: RunTests,
    ws: Workspace,
    problem: str,
    language: str,
    max_attempts: int,
) -> WorkspaceResult:
    """Tests first and frozen, then the fix retried until they pass (as ``flows.fix``)."""
    frozen: list[str] = []
    original = ws.files[ws.target]

    async def propose(
        ctx: AttemptContext, last: Candidate | None, report: TestReport | None
    ) -> Candidate:
        if report is not None and report.collection_error:
            frozen.clear()
        tests = frozen[0] if frozen else None
        prompt = workspace_fix_prompt(
            language,
            ws.target,
            ws.files,
            problem,
            ws.roots,
            ws.test_path,
            current=last.code if last else None,
            tests=tests,
            lessons=ctx.lessons,
            evidence=ctx.last_evidence,
        )
        if tests is None:
            both: WorkspaceFix = await ask(prompt, WorkspaceFix)
            frozen.append(both.tests)
            return Candidate(
                both.code, both.tests, both.explanation, tuple(both.changes)
            )
        one: WorkspaceCode = await ask(prompt, WorkspaceCode)
        return Candidate(one.code, tests, one.explanation, tuple(one.changes))

    run = await verified_loop(
        "every test passes on the fixed file",
        propose,
        lambda c: run_tests(c.code, c.tests),
        max_attempts=max_attempts,
    )
    best, report = run.best, run.report
    final = best.code if best is not None else original
    result = WorkspaceResult(
        success=run.succeeded,
        original_code=original,
        final_code=final,
        iterations=run.attempts,
        error=None
        if run.succeeded
        else (report.evidence() if report else "no candidate"),
        explanation=best.explanation if best is not None else "",
        tests_passed=report.passed if report is not None else 0,
        tests_total=report.total if report is not None else 0,
        test_code=best.tests if best is not None else "",
        changes=list(best.changes) if best is not None else [],
        loop_status=run.status,
        sandbox_seconds=run.seconds,
        target=ws.target,
        test_path=ws.test_path,
        verified=True,
        diff=unified(ws, final) if final != original else "",
    )
    if frozen:
        on_original = await run_tests(original, frozen[0])
        result.sandbox_seconds += on_original.seconds
        result.tests_reproduce_bug = not on_original.green
    return result


__all__ = ["missing_module", "unified", "unverified", "verified"]
