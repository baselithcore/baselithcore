"""
Base classes for Evaluators.
"""

import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

from .protocols import EvaluationResult, Evaluator, QualityLevel

if TYPE_CHECKING:
    from core.services.llm import LLMService

logger = get_logger(__name__)

#: Metadata key on an :class:`EvaluationResult` meaning "no judgement was
#: made" — the provider was unreachable, or its reply could not be read. The
#: accompanying score is 0.0 either way, so a caller that gates on a minimum
#: score must consult this flag or it reads an outage as a suite of failures.
JUDGE_UNAVAILABLE = "fallback"


def judge_unavailable(outcome: EvaluationResult) -> bool:
    """True when ``outcome`` records an absent judgement rather than a verdict.

    ``Evaluator`` is a protocol, so ``outcome`` is whatever an implementation
    returns; a result without metadata is read as a real judgement.

    Args:
        outcome: What an evaluator returned.

    Returns:
        Whether the judge failed to produce a judgement.
    """
    metadata = getattr(outcome, "metadata", None) or {}
    return bool(metadata.get(JUDGE_UNAVAILABLE))


class BaseLLMEvaluator(Evaluator, ABC):
    """
    Abstract base class for LLM-based evaluators.

    Handles common logic like LLM service retrieval and result parsing.
    """

    def __init__(self, llm_service: "LLMService | None" = None) -> None:
        self._llm_service: LLMService | None = llm_service

    @property
    def llm_service(self) -> "LLMService":
        """Lazy load LLM service."""
        if self._llm_service is None:
            from core.services.llm import get_llm_service

            self._llm_service = get_llm_service()
        return self._llm_service

    @abstractmethod
    def get_prompt(self, query: str, response: str, context: dict | None = None) -> str:
        """Get the evaluation prompt."""
        pass

    async def evaluate(
        self,
        response: str,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        """Evaluate response using LLM."""
        prompt = self.get_prompt(query, response, context)

        try:
            # Generate JSON response
            result_text = await self.llm_service.generate_response(prompt, json=True)
            result = self._parse_result(result_text)

            # Map score to quality
            score = result.get("score", 0.0)
            quality = self._score_to_quality(score)

            return EvaluationResult(
                score=score,
                quality=quality,
                feedback=result.get("feedback", ""),
                should_refine=result.get("should_refine", False),
                aspects=result.get("aspects", {}),
                metadata={
                    "evaluator": self.__class__.__name__,
                    # An unreadable reply is a judge that did not answer, not a
                    # judgement of zero. Gates read this flag to tell the two
                    # apart; refinement loops can keep treating the score as a
                    # score.
                    JUDGE_UNAVAILABLE: result.get("feedback") == self._UNPARSABLE,
                },
            )

        except Exception as e:
            logger.error(f"Evaluation failed in {self.__class__.__name__}: {e}")
            return self._fallback_evaluation(response, query)

    #: What `_parse_result` returns when the model's answer cannot be read as a
    #: JSON object. Callers treat a missing "score" as 0.0, so an unparsable
    #: evaluation scores zero — but it now says so in the feedback instead of
    #: arriving as a silent AttributeError inside `evaluate`.
    _UNPARSABLE = "Failed to parse evaluation result"

    def _parse_result(self, text: str) -> dict[str, Any]:
        """Parse the model's JSON answer, degrading to a scored-zero result.

        The model is asked for a JSON object, and usually sends one. Two shapes
        it also sends had no path here:

        * **valid JSON that is not an object** — a bare list, number or
          ``null``. ``json.loads`` accepted it and returned it, so `evaluate`
          then called ``.get()`` on a list, raised ``AttributeError``, and had
          it swallowed by its own broad ``except``. The evaluation became a
          zero with no indication that nothing had been evaluated.
        * **a ```json fence whose contents are malformed** — the second
          ``json.loads`` ran *inside* the first ``except`` and was unguarded,
          so its ``JSONDecodeError`` escaped into the same broad handler.

        Both now return the documented fallback, which is what the third branch
        already did for text that is not JSON at all.
        """
        candidate = text
        fenced_start = text.find("```json")
        if fenced_start != -1:
            # Prefer the fenced block when there is one: models routinely wrap
            # the object in prose, which makes the whole string unparsable.
            start = fenced_start + len("```json")
            end = text.find("```", start)
            candidate = text[start:end] if end != -1 else text[start:]

        for source in (candidate, text):
            try:
                parsed = json.loads(source)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                result: dict[str, Any] = parsed
                return result

        return {"score": 0.0, "feedback": self._UNPARSABLE}

    def _score_to_quality(self, score: float) -> QualityLevel:
        """Convert normalized score (0.0-1.0) to QualityLevel."""
        if score >= 0.9:
            return QualityLevel.EXCELLENT
        elif score >= 0.75:
            return QualityLevel.GOOD
        elif score >= 0.6:
            return QualityLevel.ACCEPTABLE
        elif score >= 0.4:
            return QualityLevel.NEEDS_IMPROVEMENT
        else:
            return QualityLevel.POOR

    def _fallback_evaluation(self, response: str, query: str) -> EvaluationResult:
        """Default fallback when the judge could not be reached or read.

        The score is 0.0 so a refinement loop keeps iterating rather than
        accepting an unchecked answer, but :data:`JUDGE_UNAVAILABLE` says the
        zero is an absence of judgement. Without that distinction a provider
        outage scored every case 0.0, and a gate comparing against a minimum
        score read a whole suite of real failures.
        """
        return EvaluationResult(
            score=0.0,
            quality=QualityLevel.POOR,
            feedback="Evaluation failed (fallback)",
            should_refine=True,
            metadata={JUDGE_UNAVAILABLE: True},
        )
