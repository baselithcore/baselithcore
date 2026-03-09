"""
Base classes for Evaluators.
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from .protocols import EvaluationResult, Evaluator, QualityLevel

from core.observability.logging import get_logger

logger = get_logger(__name__)


class BaseLLMEvaluator(Evaluator, ABC):
    """
    Abstract base class for LLM-based evaluators.

    Handles common logic like LLM service retrieval and result parsing.
    """

    def __init__(self, llm_service=None):
        self._llm_service = llm_service

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            from core.services.llm import get_llm_service

            self._llm_service = get_llm_service()
        return self._llm_service

    @abstractmethod
    def get_prompt(
        self, query: str, response: str, context: Optional[Dict] = None
    ) -> str:
        """Get the evaluation prompt."""
        pass

    async def evaluate(
        self,
        response: str,
        query: str,
        context: Optional[Dict[str, Any]] = None,
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
                metadata={"evaluator": self.__class__.__name__},
            )

        except Exception as e:
            logger.error(f"Evaluation failed in {self.__class__.__name__}: {e}")
            return self._fallback_evaluation(response, query)

    def _parse_result(self, text: str) -> Dict[str, Any]:
        """Parse LLM JSON response."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown if needed
            if "```json" in text:
                start = text.find("```json") + 7
                end = text.find("```", start)
                return json.loads(text[start:end])
            return {"score": 0.0, "feedback": "Failed to parse evaluation result"}

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
        """Default fallback when LLM fails."""
        return EvaluationResult(
            score=0.0,
            quality=QualityLevel.POOR,
            feedback="Evaluation failed (fallback)",
            should_refine=True,
            metadata={"fallback": True},
        )
