"""Run the fix-rate benchmark against a configured coding agent."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from ..agent import CodingAgent
from .cases import CASES, FixCase
from .workspace_cases import WORKSPACE_CASES, WorkspaceCase


@dataclass(frozen=True, slots=True)
class CaseResult:
    """How one case went."""

    name: str
    fixed: bool
    agent_success: bool
    attempts: int
    loop_status: str
    tests_reproduce_bug: bool | None
    seconds: float


@dataclass(frozen=True, slots=True)
class EvalReport:
    """The benchmark's outcome."""

    results: tuple[CaseResult, ...]

    @property
    def fix_rate(self) -> float:
        """Share of cases whose hidden tests pass on the returned code."""
        return (
            sum(r.fixed for r in self.results) / len(self.results)
            if self.results
            else 0.0
        )

    def render(self) -> str:
        """A plain table, one line per case, then the rate."""
        lines = [f"{'case':<18} fixed  agent  tries  loop        repro  secs"]
        for r in self.results:
            lines.append(
                f"{r.name:<18} {'yes' if r.fixed else 'no':<6} "
                f"{'ok' if r.agent_success else 'fail':<6} {r.attempts:<6} "
                f"{r.loop_status:<11} {r.tests_reproduce_bug!s:<6} {r.seconds:.1f}"
            )
        lines.append(f"fix rate: {self.fix_rate:.0%} of {len(self.results)}")
        return "\n".join(lines)


async def run_case(agent: CodingAgent, case: FixCase) -> CaseResult:
    """Fix ``case`` with ``agent``, then judge the result with the hidden tests."""
    started = time.monotonic()
    result = await agent.fix_code(case.code, case.error)
    hidden = await agent._run_tests(result.final_code, case.hidden)
    return CaseResult(
        name=case.name,
        fixed=hidden.green,
        agent_success=result.success,
        attempts=result.iterations,
        loop_status=result.loop_status,
        tests_reproduce_bug=result.tests_reproduce_bug,
        seconds=time.monotonic() - started,
    )


async def run_eval(agent: CodingAgent, cases: Sequence[FixCase] = CASES) -> EvalReport:
    """Every case, one after the other (the sandbox and the model are shared)."""
    return EvalReport(tuple([await run_case(agent, case) for case in cases]))


async def run_workspace_case(agent: CodingAgent, case: WorkspaceCase) -> CaseResult:
    """Fix one repository file, then judge it with the hidden tests in the same workspace.

    A case counts only when the fix was verified and the hidden tests pass on it.
    """
    started = time.monotonic()
    ws = case.workspace
    result = await agent.fix_in_workspace(ws, case.problem)
    hidden = await agent._run_workspace_tests(ws, result.final_code, case.hidden)
    return CaseResult(
        name=case.name,
        fixed=result.verified and hidden.green,
        agent_success=result.success,
        attempts=result.iterations,
        loop_status=result.loop_status,
        tests_reproduce_bug=result.tests_reproduce_bug,
        seconds=time.monotonic() - started,
    )


async def run_workspace_eval(
    agent: CodingAgent, cases: Sequence[WorkspaceCase] = WORKSPACE_CASES
) -> EvalReport:
    """Every workspace case, one after the other."""
    return EvalReport(tuple([await run_workspace_case(agent, c) for c in cases]))


__all__ = [
    "CaseResult",
    "EvalReport",
    "run_case",
    "run_eval",
    "run_workspace_case",
    "run_workspace_eval",
]
