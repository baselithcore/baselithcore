"""
Deprecated evaluation service shim.

``core.services.evaluation`` used to carry its own DeepEval wrapper, a
duplicate of the live evaluation stack that nothing in the runtime imported.
It shared one set of metric objects across concurrent calls (so one call could
read another's score) and exported the LLM API key into ``os.environ``. It is
retired: the names below stay for one deprecation cycle and delegate to the
live :mod:`core.evaluation` package.

Use instead:

* :class:`core.evaluation.EvaluationService` — the event-driven service;
* :class:`core.evaluation.metrics.FaithfulnessEvaluator` and
  :class:`core.evaluation.metrics.AnswerRelevancyEvaluator` — RAG metrics.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.evaluation.service import EvaluationService as _LiveEvaluationService
from core.stability import deprecated

_REMOVED_IN = "0.41.0"
_SINCE = "0.40.0"
_UNAVAILABLE = "evaluation disabled (EVAL_ENABLED) or deepeval not installed"


def _score(evaluator: Any, score: float) -> dict[str, Any]:
    metric = getattr(evaluator, "metric", None)
    if metric is None:
        return {"error": _UNAVAILABLE}
    return {
        "score": score,
        "reason": getattr(metric, "reason", None),
        "passed": score >= evaluator.threshold,
    }


@deprecated(
    since=_SINCE,
    removed_in=_REMOVED_IN,
    alternative="core.evaluation.EvaluationService",
)
class EvaluationService(_LiveEvaluationService):
    """Deprecated alias of :class:`core.evaluation.EvaluationService`.

    Keeps :meth:`evaluate_rag_response` for callers of the retired DeepEval
    wrapper, now backed by the live metric evaluators with fresh metric
    objects per call.
    """

    def __init__(self, *args: Any, use_openai: bool = True, **kwargs: Any) -> None:
        """Build the live service; ``use_openai`` is accepted and ignored.

        Args:
            *args: Forwarded to :class:`core.evaluation.EvaluationService`.
            use_openai: Ignored. The judge model now comes from
                ``EvaluationConfig.model``; no credential is exported to the
                process environment.
            **kwargs: Forwarded to :class:`core.evaluation.EvaluationService`.
        """
        del use_openai
        super().__init__(*args, **kwargs)

    async def evaluate_rag_response(
        self,
        query: str,
        response: str,
        retrieved_context: list[str],
        expected_output: str | None = None,
    ) -> dict[str, Any]:
        """Score faithfulness and answer relevancy of a RAG answer.

        Args:
            query: The original user question.
            response: The generated answer.
            retrieved_context: The chunks the answer was generated from.
            expected_output: Accepted for compatibility; the contextual
                precision/recall metrics it enabled are not carried over.

        Returns:
            ``{"faithfulness": {...}, "answer_relevancy": {...}}``, each a
            ``{"score", "reason", "passed"}`` mapping or ``{"error": ...}``.
        """
        del expected_output
        from core.evaluation.metrics import (
            AnswerRelevancyEvaluator,
            FaithfulnessEvaluator,
        )

        # Fresh evaluators per call: DeepEval metrics store score/reason on
        # the instance, so sharing them across concurrent calls mixes results.
        faithfulness = FaithfulnessEvaluator()
        relevancy = AnswerRelevancyEvaluator()
        f_score, r_score = await asyncio.gather(
            asyncio.to_thread(faithfulness.measure, query, response, retrieved_context),
            asyncio.to_thread(relevancy.measure, query, response),
        )
        return {
            "faithfulness": _score(faithfulness, f_score),
            "answer_relevancy": _score(relevancy, r_score),
        }


_eval_service: EvaluationService | None = None


@deprecated(
    since=_SINCE,
    removed_in=_REMOVED_IN,
    alternative="core.evaluation.EvaluationService",
)
def get_evaluation_service() -> EvaluationService:
    """Return the process-wide deprecated :class:`EvaluationService`."""
    global _eval_service
    if _eval_service is None:
        _eval_service = EvaluationService()
    return _eval_service


__all__ = ["EvaluationService", "get_evaluation_service"]
