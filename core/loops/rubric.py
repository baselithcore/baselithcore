"""Rubric-graded verification for soft loop goals.

:class:`~core.loops.engineered.EngineeredLoop` demands a machine-checkable
termination condition, which shell-checkable goals (tests green, file
exists) satisfy directly — but "the summary is clear and complete" has no
exit code. A :class:`RubricJudge` turns a caller-supplied rubric into an
LLM-graded score, and :func:`rubric_verifier` adapts it to the loop's
``Verifier`` signature ``() -> (done, evidence)``.

Deterministic verifiers remain the recommended default: a graded rubric is
a weaker oracle (it can be gamed by a search and drifts with the judge
model). Reach for it only when no deterministic check exists.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.evaluation.base import BaseLLMEvaluator
from core.loops.engineered import Verifier

DEFAULT_RUBRIC_THRESHOLD = 0.8


class RubricJudge(BaseLLMEvaluator):
    """LLM judge grading an output against a caller-supplied rubric."""

    def __init__(self, rubric: str, llm_service: Any | None = None) -> None:
        super().__init__(llm_service)
        self._rubric = rubric

    def get_prompt(
        self, query: str, response: str, context: dict[str, Any] | None = None
    ) -> str:
        """Build the grading prompt (reasoning first, then the score)."""
        return f"""You are a strict Rubric Judge. Grade the output below against the rubric.

GOAL:
{query}

RUBRIC (the output must satisfy every point):
{self._rubric}

OUTPUT:
{response}

Write your reasoning FIRST, then score 0.0 to 1.0 (1.0 = every rubric point fully satisfied).

Return JSON with the keys in this exact order:
{{
    "feedback": "<point-by-point reasoning for the grade>",
    "score": <float>,
    "should_refine": <boolean>
}}"""


def rubric_verifier(
    rubric: str,
    output_provider: Callable[[], str],
    *,
    goal: str,
    threshold: float = DEFAULT_RUBRIC_THRESHOLD,
    llm_service: Any | None = None,
) -> Verifier:
    """Adapt a rubric grade to the loop's ``(done, evidence)`` contract.

    Args:
        rubric: The grading rubric, one requirement per line.
        output_provider: Zero-arg callable returning the current candidate
            output (called fresh on every verification).
        goal: The loop goal, given to the judge as context.
        threshold: Minimum score that counts as done.
        llm_service: Judge LLM override (tests / cheap-tier routing).

    Returns:
        An async verifier; its evidence carries the score and the judge's
        point-by-point feedback so failed attempts feed the lesson log.
    """
    judge = RubricJudge(rubric, llm_service=llm_service)

    async def verify() -> tuple[bool, str]:
        output = output_provider()
        result = await judge.evaluate(output, goal)
        evidence = f"rubric score {result.score:.2f} (threshold {threshold}): " + (
            result.feedback or ""
        )
        return result.score >= threshold, evidence

    return verify


__all__ = ["DEFAULT_RUBRIC_THRESHOLD", "RubricJudge", "rubric_verifier"]
