"""Multi-judge consensus evaluation.

A single LLM judge agrees with itself only ~70% of the time on borderline
cases: one grade is a sample of one. Running the *same* question past
several independent judges and taking the majority verdict removes most of
that noise, and the spread between them is itself a signal — wide
disagreement marks the case a human should look at.

This is deliberately different from
:class:`~core.evaluation.judges.CompositeEvaluator`, which averages judges
that grade *different aspects* (relevance, coherence, …). Here every judge
answers the *same* question, so majority and median are meaningful.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from statistics import median
from typing import Any

from core.evaluation.protocols import EvaluationResult, Evaluator, QualityLevel
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["ConsensusEvaluator"]


def _score_to_quality(score: float) -> QualityLevel:
    """Map a normalized score to a :class:`QualityLevel`."""
    if score >= 0.9:
        return QualityLevel.EXCELLENT
    if score >= 0.75:
        return QualityLevel.GOOD
    if score >= 0.6:
        return QualityLevel.ACCEPTABLE
    if score >= 0.4:
        return QualityLevel.NEEDS_IMPROVEMENT
    return QualityLevel.POOR


class ConsensusEvaluator:
    """Grade one response with several judges and take the majority verdict.

    Args:
        judges: Two or more evaluators answering the *same* question.
            Diversity is the point — different models, or the same model at
            different temperatures, beat N identical calls.
        disagreement_threshold: Score spread (max - min) above which the
            panel is flagged as split in the result metadata.

    The aggregated score is the **median**, not the mean: one judge that
    misreads the case cannot drag the panel with it. ``should_refine`` is a
    strict majority vote; a tie resolves to refine, because the cheaper
    mistake is one extra refinement pass.

    Example::

        panel = ConsensusEvaluator([
            RelevanceEvaluator(llm_service=sonnet),
            RelevanceEvaluator(llm_service=gemini),
            RelevanceEvaluator(llm_service=haiku),
        ])
        result = await panel.evaluate(answer, question)
        if result.metadata["disagreement"] > 0.3:
            escalate_to_human(result)
    """

    def __init__(
        self,
        judges: Sequence[Evaluator],
        *,
        disagreement_threshold: float = 0.3,
    ) -> None:
        if len(judges) < 2:
            raise ValueError("ConsensusEvaluator needs at least 2 judges")
        self._judges = list(judges)
        self._disagreement_threshold = disagreement_threshold

    @property
    def judges(self) -> list[Evaluator]:
        """The panel, in call order."""
        return list(self._judges)

    async def evaluate(
        self,
        response: str,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        """Run the panel concurrently and aggregate its verdicts.

        Args:
            response: The response under evaluation.
            query: The original user query.
            context: Optional additional context passed to every judge.

        Returns:
            An :class:`EvaluationResult` whose score is the panel median and
            whose metadata carries the per-judge scores, the vote split and
            the disagreement spread. Judges that raise are dropped from the
            panel rather than failing the whole evaluation; if every judge
            raises, the result is a POOR score flagged ``consensus_failed``.
        """
        settled = await asyncio.gather(
            *(judge.evaluate(response, query, context) for judge in self._judges),
            return_exceptions=True,
        )

        scores: list[float] = []
        refine_votes = 0
        feedbacks: list[str] = []
        per_judge: dict[str, float] = {}
        failed: list[str] = []

        for judge, outcome in zip(self._judges, settled):
            name = type(judge).__name__
            if isinstance(outcome, BaseException):
                logger.warning("Consensus judge %s failed: %s", name, outcome)
                failed.append(name)
                continue
            scores.append(outcome.score)
            per_judge[f"{name}#{len(scores)}"] = outcome.score
            if outcome.should_refine:
                refine_votes += 1
            if outcome.feedback:
                feedbacks.append(f"{name}: {outcome.feedback}")

        if not scores:
            return EvaluationResult(
                score=0.0,
                quality=QualityLevel.POOR,
                feedback="Every judge in the consensus panel failed.",
                should_refine=True,
                metadata={"type": "consensus", "consensus_failed": True, "failed": failed},
            )

        agreed = median(scores)
        spread = max(scores) - min(scores)
        # Tie resolves to refine: an unnecessary refinement pass is cheaper
        # than shipping an answer half the panel rejected.
        should_refine = refine_votes * 2 >= len(scores)

        return EvaluationResult(
            score=agreed,
            quality=_score_to_quality(agreed),
            feedback=" | ".join(feedbacks),
            should_refine=should_refine,
            aspects=per_judge,
            metadata={
                "type": "consensus",
                "judges": len(self._judges),
                "voted": len(scores),
                "failed": failed,
                "refine_votes": refine_votes,
                "disagreement": spread,
                "split": spread > self._disagreement_threshold,
                "scores": scores,
            },
        )
