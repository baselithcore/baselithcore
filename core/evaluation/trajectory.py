"""
Trajectory-aware evaluation for agent runs.

Evaluates not just the final answer but the sequence of tool calls the
agent made to get there. A run is judged on keyword presence, forbidden
token absence, expected/forbidden tool calls, tool-call budget, and
latency.

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
    cost_usd: float


class TrajectoryCase(TypedDict, total=False):
    """Declarative test case for a trajectory eval."""

    case_id: str
    input: str
    expected_keywords: list[str]
    forbidden_keywords: list[str]
    expected_tools: list[str]
    forbidden_tools: list[str]
    # Argument-level assertion: tool name -> required arg subset. The tool must
    # be called at least once with args that are a superset of this mapping.
    expected_tool_args: dict[str, dict[str, object]]
    # The listed tools must appear in this relative order (as a subsequence of
    # the actual call sequence — intervening calls are allowed).
    expected_tool_order: list[str]
    max_tool_calls: int
    max_latency_ms: int
    max_cost_usd: float


def _args_match(actual: dict[str, object], required: dict[str, object]) -> bool:
    """True when every required key is present in ``actual`` with an equal value."""
    return all(actual.get(k) == v for k, v in required.items())


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """True when ``needle`` occurs in ``haystack`` in order (gaps allowed)."""
    it = iter(haystack)
    return all(item in it for item in needle)


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
    cost_usd: float = 0.0
    # Partial credit in [0, 1]: fraction of evaluated assertions that passed.
    # 1.0 for a clean pass; lets aggregation track near-misses, not just pass/fail.
    score: float = 1.0


class TrajectoryEvaluator:
    """Pure evaluator: given a case and a captured run, return a result."""

    def evaluate(
        self,
        case: TrajectoryCase,
        output_text: str,
        trajectory: list[ToolCall],
        latency_ms: int,
        cost_usd: float = 0.0,
    ) -> TrajectoryResult:
        """Return pass/fail with itemized violations.

        ``cost_usd`` is the total run cost. When zero, it is summed from any
        ``cost_usd`` recorded on individual tool calls so callers can supply
        cost at either granularity.
        """
        violations: list[TrajectoryViolation] = []
        # Count every assertion evaluated so the result can carry partial credit
        # (score = passed_checks / total_checks), not just a binary pass/fail.
        checks = 0

        total_cost = cost_usd
        if total_cost <= 0.0:
            total_cost = sum(t.get("cost_usd", 0.0) for t in trajectory)

        text_lc = output_text.lower()
        for kw in case.get("expected_keywords", []):
            checks += 1
            if kw.lower() not in text_lc:
                violations.append(TrajectoryViolation("expected_keyword_missing", kw))
        for kw in case.get("forbidden_keywords", []):
            checks += 1
            if kw.lower() in text_lc:
                violations.append(TrajectoryViolation("forbidden_keyword_present", kw))

        called = {t["name"] for t in trajectory if "name" in t}
        for tool in case.get("expected_tools", []):
            checks += 1
            if tool not in called:
                violations.append(TrajectoryViolation("expected_tool_not_called", tool))
        for tool in case.get("forbidden_tools", []):
            checks += 1
            if tool in called:
                violations.append(TrajectoryViolation("forbidden_tool_called", tool))

        # Argument-level match: the tool must be called at least once with args
        # that are a superset of the required subset (not just present by name).
        for tool_name, required in (case.get("expected_tool_args") or {}).items():
            checks += 1
            matched = any(
                t.get("name") == tool_name
                and _args_match(t.get("args") or {}, required)
                for t in trajectory
            )
            if not matched:
                violations.append(
                    TrajectoryViolation(
                        "tool_args_mismatch",
                        f"{tool_name} never called with args ⊇ {required}",
                    )
                )

        # Order match: the expected tools must appear as a subsequence of the
        # actual call sequence (intervening calls allowed).
        expected_order = case.get("expected_tool_order")
        if expected_order:
            checks += 1
            actual_sequence = [t["name"] for t in trajectory if "name" in t]
            if not _is_subsequence(expected_order, actual_sequence):
                violations.append(
                    TrajectoryViolation(
                        "tool_order_mismatch",
                        f"expected order {expected_order} not a subsequence of "
                        f"{actual_sequence}",
                    )
                )

        max_calls = case.get("max_tool_calls")
        if max_calls is not None:
            checks += 1
            if len(trajectory) > max_calls:
                violations.append(
                    TrajectoryViolation(
                        "max_tool_calls_exceeded",
                        f"{len(trajectory)} > {max_calls}",
                    )
                )

        max_lat = case.get("max_latency_ms")
        if max_lat is not None:
            checks += 1
            if latency_ms > max_lat:
                violations.append(
                    TrajectoryViolation(
                        "max_latency_exceeded",
                        f"{latency_ms}ms > {max_lat}ms",
                    )
                )

        max_cost = case.get("max_cost_usd")
        if max_cost is not None:
            checks += 1
            if total_cost > max_cost:
                violations.append(
                    TrajectoryViolation(
                        "max_cost_exceeded",
                        f"${total_cost:.4f} > ${max_cost:.4f}",
                    )
                )

        score = (checks - len(violations)) / checks if checks else 1.0
        return TrajectoryResult(
            case_id=case.get("case_id", "unknown"),
            passed=(len(violations) == 0),
            violations=violations,
            tool_calls=len(trajectory),
            latency_ms=latency_ms,
            cost_usd=total_cost,
            score=score,
        )


def aggregate_pass_rate(results: list[TrajectoryResult]) -> float:
    """Return pass rate across a list of results. Empty list returns 0.0."""
    if not results:
        return 0.0
    passed = sum(1 for r in results if r.passed)
    return passed / len(results)
