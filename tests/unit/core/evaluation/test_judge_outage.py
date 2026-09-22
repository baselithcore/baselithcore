"""A judge outage keeps the deterministic verdict instead of failing the suite.

The runner documents this and implements it with an ``except`` around the judge
call — but the shipped evaluators catch their own provider errors and answer
with a scored-zero ``EvaluationResult``, so that ``except`` never fired for the
failure it was written for. A provider outage scored every sample 0.0, each
median landed under ``judge_min_score``, and the nightly gate reported the
whole corpus as a regression: exactly the flake-turns-CI-red outcome the design
says cannot happen.

These tests drive the *real* evaluator against a broken LLM service rather than
a stub that raises, because a stub that raises exercises a path production
never reaches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from core.evaluation.base import BaseLLMEvaluator, judge_unavailable
from core.evaluation.regression_runner import RecordedRun, run_regression_async


class _Judge(BaseLLMEvaluator):
    """The real base evaluator, with the LLM service injected."""

    def get_prompt(self, query: str, response: str, context: dict | None = None) -> str:
        return f"{query}\n{response}"


def _llm(*, reply: str | None = None, error: Exception | None = None) -> MagicMock:
    service = MagicMock()
    service.generate_response = AsyncMock(
        side_effect=error, return_value=reply or '{"score": 0.9}'
    )
    return service


def _case(case_id: str = "c1") -> dict:
    return {"case_id": case_id, "input": "question?"}


def _run(case_id: str = "c1") -> RecordedRun:
    return RecordedRun(
        case_id=case_id, output_text="a fine answer", trajectory=[], latency_ms=10
    )


class TestEvaluatorMarksAbsentJudgements:
    async def test_provider_outage_is_marked_unavailable(self) -> None:
        outcome = await _Judge(_llm(error=RuntimeError("503"))).evaluate("a", "q")

        assert outcome.score == 0.0
        assert judge_unavailable(outcome)

    async def test_unreadable_reply_is_marked_unavailable(self) -> None:
        outcome = await _Judge(_llm(reply="I think it was fine, honestly")).evaluate(
            "a", "q"
        )

        assert judge_unavailable(outcome)

    async def test_a_genuine_zero_is_not_marked_unavailable(self) -> None:
        """A judge that read the answer and hated it is a verdict, not an outage."""
        outcome = await _Judge(
            _llm(reply='{"score": 0.0, "feedback": "wrong"}')
        ).evaluate("a", "q")

        assert outcome.score == 0.0
        assert not judge_unavailable(outcome)


class TestGateBehaviourUnderOutage:
    async def test_outage_keeps_the_deterministic_pass(self) -> None:
        report = await run_regression_async(
            [_case()],
            {"c1": _run()},
            judge=_Judge(_llm(error=RuntimeError("503"))),
            judge_samples=3,
        )

        assert report.passed == 1
        assert report.judge_errors == ["c1"]
        assert report.judge_scores == {}

    async def test_a_real_low_score_still_fails_the_case(self) -> None:
        report = await run_regression_async(
            [_case()],
            {"c1": _run()},
            judge=_Judge(_llm(reply='{"score": 0.1, "feedback": "wrong"}')),
            judge_samples=1,
        )

        assert report.passed == 0
        assert report.judge_errors == []
