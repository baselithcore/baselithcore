"""
Interaction Protocols for Reflection Components.

Defines the formal interfaces required for pluggable evaluators and
refiners within the reflection framework, ensuring strict type safety
and modularity.
"""

from typing import Protocol

from core.evaluation.protocols import EvaluationResult, Evaluator, QualityLevel

# Aliases for backward compatibility
SelfEvaluator = Evaluator

__all__ = ["EvaluationResult", "QualityLevel", "Refiner", "SelfEvaluator"]


class Refiner(Protocol):
    """Protocol for response refiners.

    Implementations take a response and feedback to produce
    an improved version.
    """

    async def refine(
        self,
        response: str,
        feedback: str,
        query: str,
        context: dict | None = None,
    ) -> str:
        """
        Refine a response based on feedback.

        Args:
            response: The original response to refine
            feedback: Feedback describing improvements needed
            query: The original user query
            context: Optional additional context

        Returns:
            Refined response string
        """
        ...
