"""Multi-judge consensus: median score, majority vote, disagreement signal."""

import pytest

from core.evaluation.consensus import ConsensusEvaluator
from core.evaluation.protocols import EvaluationResult, QualityLevel


class _StubJudge:
    """Judge that returns a fixed verdict (or raises)."""

    def __init__(self, score: float, should_refine: bool = False, boom: bool = False):
        self._score = score
        self._refine = should_refine
        self._boom = boom

    async def evaluate(self, response, query, context=None):
        if self._boom:
            raise RuntimeError("judge unavailable")
        return EvaluationResult(
            score=self._score,
            quality=QualityLevel.GOOD,
            feedback=f"score {self._score}",
            should_refine=self._refine,
        )


class TestConsensusEvaluator:
    def test_needs_at_least_two_judges(self):
        with pytest.raises(ValueError):
            ConsensusEvaluator([_StubJudge(0.8)])

    async def test_score_is_the_median_not_the_mean(self):
        # One judge misreads the case badly; the median must ignore it,
        # where a mean would drag the panel down to 0.57.
        panel = ConsensusEvaluator([_StubJudge(0.8), _StubJudge(0.82), _StubJudge(0.1)])
        result = await panel.evaluate("answer", "question")
        assert result.score == 0.8

    async def test_majority_vote_decides_refinement(self):
        panel = ConsensusEvaluator(
            [
                _StubJudge(0.5, should_refine=True),
                _StubJudge(0.6, should_refine=True),
                _StubJudge(0.9, should_refine=False),
            ]
        )
        result = await panel.evaluate("answer", "question")
        assert result.should_refine is True
        assert result.metadata["refine_votes"] == 2

    async def test_tie_resolves_to_refine(self):
        panel = ConsensusEvaluator(
            [_StubJudge(0.7, should_refine=True), _StubJudge(0.7, should_refine=False)]
        )
        result = await panel.evaluate("answer", "question")
        assert result.should_refine is True

    async def test_wide_disagreement_is_flagged(self):
        panel = ConsensusEvaluator([_StubJudge(0.95), _StubJudge(0.2)])
        result = await panel.evaluate("answer", "question")
        assert result.metadata["split"] is True
        assert result.metadata["disagreement"] == pytest.approx(0.75)

    async def test_agreement_is_not_flagged(self):
        panel = ConsensusEvaluator([_StubJudge(0.80), _StubJudge(0.85)])
        result = await panel.evaluate("answer", "question")
        assert result.metadata["split"] is False

    async def test_a_failing_judge_is_dropped_not_fatal(self):
        panel = ConsensusEvaluator(
            [_StubJudge(0.8), _StubJudge(0.0, boom=True), _StubJudge(0.9)]
        )
        result = await panel.evaluate("answer", "question")
        assert result.metadata["voted"] == 2
        assert result.metadata["failed"] == ["_StubJudge"]
        assert result.score == pytest.approx(0.85)

    async def test_total_panel_failure_is_reported_as_poor(self):
        panel = ConsensusEvaluator(
            [_StubJudge(0.0, boom=True), _StubJudge(0.0, boom=True)]
        )
        result = await panel.evaluate("answer", "question")
        assert result.metadata["consensus_failed"] is True
        assert result.quality is QualityLevel.POOR
        assert result.should_refine is True
