"""
Trajectory-aware evaluation for agent runs.

Book ch2/ch10: evaluate not just the final answer but the sequence of tool
calls the agent made to get there. A run is judged on keyword presence,
forbidden token absence, expected/forbidden tool calls, tool-call budget,
and latency.

Integration hook: ``core.evaluation.service`` registers ``TrajectoryEvaluator``
as a first-class evaluator. CI runs the registered suite via the script
``scripts/run_prompt_regression.py`` (see plan P3.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class ToolCall(TypedDict, total=False):
    """A single tool invocation captured during an agent run."""

    name: str
    args: dict[str, object]
    ok: bool
    latency_ms: int


class TrajectoryCase(TypedDict, total=False):
    """Declarative test case for a trajectory eval."""

    case_id: str
    input: str
    expected_keywords: list[str]
    forbidden_keywords: list[str]
    expected_tools: list[str]
    forbidden_tools: list[str]
    max_tool_calls: int
    max_latency_ms: int


@dataclass(frozen=True)
class TrajectoryViolation:
    """A single rule violation discovered during evaluation."""

    rule: str
    detail: str


@dataclass
class TrajectoryResult:
    """Outcome of a trajectory evaluation against one case."""

    case_id: str
    passed: bool
    violations: list[TrajectoryViolation] = field(default_factory=list)
    tool_calls: int = 0
    latency_ms: int = 0


class TrajectoryEvaluator:
    """Pure evaluator: given a case and a captured run, return a result."""

    def evaluate(
        self,
        case: TrajectoryCase,
        output_text: str,
        trajectory: list[ToolCall],
        latency_ms: int,
    ) -> TrajectoryResult:
        """Return pass/fail with itemized violations."""
        violations: list[TrajectoryViolation] = []

        text_lc = output_text.lower()
        for kw in case.get("expected_keywords", []):
            if kw.lower() not in text_lc:
                violations.append(TrajectoryViolation("expected_keyword_missing", kw))
        for kw in case.get("forbidden_keywords", []):
            if kw.lower() in text_lc:
                violations.append(TrajectoryViolation("forbidden_keyword_present", kw))

        called = {t["name"] for t in trajectory if "name" in t}
        for tool in case.get("expected_tools", []):
            if tool not in called:
                violations.append(TrajectoryViolation("expected_tool_not_called", tool))
        for tool in case.get("forbidden_tools", []):
            if tool in called:
                violations.append(TrajectoryViolation("forbidden_tool_called", tool))

        max_calls = case.get("max_tool_calls")
        if max_calls is not None and len(trajectory) > max_calls:
            violations.append(
                TrajectoryViolation(
                    "max_tool_calls_exceeded",
                    f"{len(trajectory)} > {max_calls}",
                )
            )

        max_lat = case.get("max_latency_ms")
        if max_lat is not None and latency_ms > max_lat:
            violations.append(
                TrajectoryViolation(
                    "max_latency_exceeded",
                    f"{latency_ms}ms > {max_lat}ms",
                )
            )

        return TrajectoryResult(
            case_id=case.get("case_id", "unknown"),
            passed=(len(violations) == 0),
            violations=violations,
            tool_calls=len(trajectory),
            latency_ms=latency_ms,
        )


def aggregate_pass_rate(results: list[TrajectoryResult]) -> float:
    """Return pass rate across a list of results. Empty list returns 0.0."""
    if not results:
        return 0.0
    passed = sum(1 for r in results if r.passed)
    return passed / len(results)
