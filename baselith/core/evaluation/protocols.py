"""
Core Evaluation Protocols and Types.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class QualityLevel(Enum):
    """Quality assessment levels."""

    EXCELLENT = "excellent"
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    NEEDS_IMPROVEMENT = "needs_improvement"
    POOR = "poor"


def quality_for_score(score: float) -> QualityLevel:
    """Map a normalized score (0.0-1.0) to its :class:`QualityLevel` band.

    The one banding every evaluator shares: the LLM judges and the online
    deterministic checks report comparable quality levels.
    """
    if score >= 0.9:
        return QualityLevel.EXCELLENT
    if score >= 0.75:
        return QualityLevel.GOOD
    if score >= 0.6:
        return QualityLevel.ACCEPTABLE
    if score >= 0.4:
        return QualityLevel.NEEDS_IMPROVEMENT
    return QualityLevel.POOR


@dataclass
class EvaluationResult:
    """Result of a response evaluation."""

    score: float  # 0.0 to 1.0 (normalized)
    quality: QualityLevel
    feedback: str
    should_refine: bool = False
    aspects: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_acceptable(self) -> bool:
        """Check if quality is acceptable or better."""
        return self.quality in (
            QualityLevel.EXCELLENT,
            QualityLevel.GOOD,
            QualityLevel.ACCEPTABLE,
        )


@runtime_checkable
class Evaluator(Protocol):
    """
    Protocol for response evaluators.
    """

    async def evaluate(
        self,
        response: str,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        """
        Evaluate the quality of a response.

        Args:
            response: The LLM response to evaluate
            query: The original user query
            context: Optional additional context (sources, history)

        Returns:
            EvaluationResult with quality assessment
        """
        ...
