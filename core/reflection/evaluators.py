"""
Performance Evaluators for Self-Reflection.

Provides implementations for assessing the semantic quality of agent
responses. Evaluates across dimensions such as relevance, accuracy,
clarity, and helpfulness to drive the refinement process.
"""

import json
from core.observability.logging import get_logger
from typing import Any, Dict, Optional

from core.evaluation.base import BaseLLMEvaluator
from .protocols import EvaluationResult, QualityLevel

logger = get_logger(__name__)

# Evaluation prompt template
EVALUATION_PROMPT = """Evaluate the quality of this AI response to the user's query.

USER QUERY:
{query}

AI RESPONSE:
{response}

Evaluate on these criteria (1-5 scale each):
1. RELEVANCE: Does it directly address the query?
2. ACCURACY: Is the information factually correct?
3. COMPLETENESS: Does it cover all aspects of the query?
4. CLARITY: Is it well-structured and easy to understand?
5. HELPFULNESS: Does it provide actionable value?

Respond in this exact JSON format:
{{
    "relevance": <1-5>,
    "accuracy": <1-5>,
    "completeness": <1-5>,
    "clarity": <1-5>,
    "helpfulness": <1-5>,
    "overall_score": <0.0-1.0>,
    "feedback": "<specific improvement suggestions>",
    "should_refine": <true/false>
}}"""


class DefaultEvaluator(BaseLLMEvaluator):
    """
    Standard LLM-powered quality assessor.

    Utilizes a structured prompting strategy to extract multi-dimensional
    scores and actionable natural language feedback from a critic LLM.
    Supports fallback heuristics for robustness during network failures.
    """

    def get_prompt(
        self, query: str, response: str, context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Build evaluation prompt."""
        return EVALUATION_PROMPT.format(query=query, response=response)

    def _parse_response(self, evaluation_text: str) -> EvaluationResult:
        """Parse LLM JSON response."""
        result = json.loads(evaluation_text)

        # Calculate overall score from aspects
        aspects = {
            "relevance": result.get("relevance", 3),
            "accuracy": result.get("accuracy", 3),
            "completeness": result.get("completeness", 3),
            "clarity": result.get("clarity", 3),
            "helpfulness": result.get("helpfulness", 3),
        }

        # Normalize to 0-1 scale
        avg_score = sum(aspects.values()) / (5 * len(aspects))
        overall_score = result.get("overall_score", avg_score)

        # Determine quality level
        quality = self._score_to_quality(overall_score)

        return EvaluationResult(
            quality=quality,
            score=overall_score,
            feedback=result.get("feedback", ""),
            should_refine=result.get("should_refine", overall_score < 0.7),
            aspects=aspects,
        )

    def _score_to_quality(self, score: float) -> QualityLevel:
        """Convert numeric score to quality level."""
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

    def _fallback_evaluation(
        self,
        response: str,
        query: str,
    ) -> EvaluationResult:
        """Fallback evaluation when LLM is unavailable."""
        # Simple heuristic-based evaluation
        score = 0.5

        # Check response length
        if len(response) < 50:
            score -= 0.2
        elif len(response) > 200:
            score += 0.1

        # Check if response mentions query terms
        query_terms = set(query.lower().split())
        response_terms = set(response.lower().split())
        overlap = len(query_terms & response_terms) / max(len(query_terms), 1)
        score += overlap * 0.2

        score = max(0.0, min(1.0, score))

        return EvaluationResult(
            quality=self._score_to_quality(score),
            score=score,
            feedback="Fallback evaluation - LLM unavailable",
            should_refine=score < 0.6,
        )
