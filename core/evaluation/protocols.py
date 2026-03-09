"""
Core Evaluation Protocols and Types.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Protocol, runtime_checkable


class QualityLevel(Enum):
    """Quality assessment levels."""

    EXCELLENT = "excellent"
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    NEEDS_IMPROVEMENT = "needs_improvement"
    POOR = "poor"


@dataclass
class EvaluationResult:
    """Result of a response evaluation."""

    score: float  # 0.0 to 1.0 (normalized)
    quality: QualityLevel
    feedback: str
    should_refine: bool = False
    aspects: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

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
        context: Optional[Dict[str, Any]] = None,
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
