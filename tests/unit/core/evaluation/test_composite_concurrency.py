"""
Tests for ``CompositeEvaluator`` concurrent sub-judge execution.

The composite runs independent LLM judges; it must fan them out
concurrently (via ``asyncio.gather``) while preserving deterministic
ordering and aggregation of the per-aspect results.
"""

from __future__ import annotations

import asyncio

import pytest

from core.evaluation.judges import CompositeEvaluator
from core.evaluation.protocols import EvaluationResult, QualityLevel


class _SlowEvaluator:
    """Fake judge that sleeps, records overlap, and returns a fixed score."""

    def __init__(self, score: float, refine: bool, tracker: dict) -> None:
        self._score = score
        self._refine = refine
        self._tracker = tracker

    async def evaluate(
        self, response: str, query: str, context=None
    ) -> EvaluationResult:
        self._tracker["active"] += 1
        self._tracker["max_active"] = max(
            self._tracker["max_active"], self._tracker["active"]
        )
        try:
            await asyncio.sleep(0.05)
        finally:
            self._tracker["active"] -= 1
        return EvaluationResult(
            score=self._score,
            quality=QualityLevel.GOOD,
            feedback=f"fb-{self._score}",
            should_refine=self._refine,
        )


# Names drive the aspect keys (class name minus "Evaluator", lowercased).
class FirstEvaluator(_SlowEvaluator):
    pass


class SecondEvaluator(_SlowEvaluator):
    pass


class ThirdEvaluator(_SlowEvaluator):
    pass


class TestCompositeConcurrency:
    async def test_runs_judges_concurrently(self) -> None:
        tracker = {"active": 0, "max_active": 0}
        evaluators = [
            FirstEvaluator(0.2, False, tracker),
            SecondEvaluator(0.4, False, tracker),
            ThirdEvaluator(0.6, False, tracker),
        ]
        composite = CompositeEvaluator(evaluators=evaluators)

        loop = asyncio.get_running_loop()
        start = loop.time()
        await composite.evaluate(response="r", query="q")
        elapsed = loop.time() - start

        # All three overlapped (sequential would peak at 1 active at a time).
        assert tracker["max_active"] == 3
        # And wall time is ~one sleep, not three.
        assert elapsed < 0.12

    async def test_preserves_ordering_and_aggregation(self) -> None:
        tracker = {"active": 0, "max_active": 0}
        evaluators = [
            FirstEvaluator(0.2, False, tracker),
            SecondEvaluator(0.4, True, tracker),
            ThirdEvaluator(0.6, False, tracker),
        ]
        composite = CompositeEvaluator(evaluators=evaluators)

        result = await composite.evaluate(response="r", query="q")

        # Average is identical to the serial computation.
        assert result.score == pytest.approx((0.2 + 0.4 + 0.6) / 3)
        # Per-aspect scores keyed by judge name, order preserved.
        assert result.aspects == {"first": 0.2, "second": 0.4, "third": 0.6}
        assert list(result.aspects.keys()) == ["first", "second", "third"]
        assert result.feedback == "first: fb-0.2 | second: fb-0.4 | third: fb-0.6"
        # should_refine is the OR across judges.
        assert result.should_refine is True
        assert result.metadata == {"type": "composite"}

    async def test_empty_evaluators_yields_zero(self) -> None:
        # Note: the constructor treats a falsy ``evaluators`` as "use defaults",
        # so set the empty list explicitly to exercise the no-judge guard.
        composite = CompositeEvaluator()
        composite.evaluators = []
        result = await composite.evaluate(response="r", query="q")
        assert result.score == 0.0
        assert result.aspects == {}
