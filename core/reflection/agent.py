"""
RelvectionAgent - Iterative Quality Refinement.

Implements the Reflection agentic design pattern, enabling agents to
self-evaluate their outputs and perform targeted improvements based on
specific feedback loops.
"""

from typing import Optional, Tuple, TYPE_CHECKING

from core.observability.logging import get_logger

from .protocols import (
    SelfEvaluator,
    Refiner,
    EvaluationResult,
)

if TYPE_CHECKING:
    from core.config.reflection import ReflectionConfig

logger = get_logger(__name__)


class ReflectionAgent:
    """
    Controller for the iterative reflection and refinement loop.

    Coordinates the quality assessment of an initial response and
    manages the subsequent refinement steps. Prevents infinite loops via
    max iteration limits and detects performance stagnation to exit
    early if no improvements are being made.
    """

    DEFAULT_MAX_ITERATIONS = 3
    DEFAULT_QUALITY_THRESHOLD = 0.7

    def __init__(
        self,
        evaluator: SelfEvaluator,
        refiner: Refiner,
        max_iterations: Optional[int] = None,
        quality_threshold: Optional[float] = None,
        config: Optional["ReflectionConfig"] = None,
    ):
        """
        Initialize ReflectionAgent.

        Args:
            evaluator: SelfEvaluator implementation for quality assessment
            refiner: Refiner implementation for response improvement
            max_iterations: Maximum refinement iterations (overrides config)
            quality_threshold: Minimum acceptable quality score (overrides config)
            config: ReflectionConfig instance, uses global if not provided
        """
        if config is None:
            from core.config.reflection import get_reflection_config

            config = get_reflection_config()

        self.evaluator = evaluator
        self.refiner = refiner
        self.max_iterations = (
            max_iterations if max_iterations is not None else config.max_iterations
        )
        self.quality_threshold = (
            quality_threshold
            if quality_threshold is not None
            else config.quality_threshold
        )

    async def reflect(
        self,
        response: str,
        query: str,
        context: Optional[dict] = None,
    ) -> Tuple[str, EvaluationResult, int]:
        """
        Evaluate and iteratively refine a response.

        Args:
            response: Initial LLM response
            query: Original user query
            context: Optional additional context

        Returns:
            Tuple of (final_response, final_evaluation, iterations_used)
        """
        current_response = response
        iterations = 0
        last_score = 0.0

        for i in range(self.max_iterations):
            iterations = i + 1

            # Evaluate current response
            evaluation = await self.evaluate(current_response, query, context)

            logger.info(
                f"Reflection iteration {iterations}: "
                f"score={evaluation.score:.2f}, quality={evaluation.quality.value}"
            )

            # Check if quality is acceptable
            if evaluation.score >= self.quality_threshold:
                logger.info(f"Quality threshold met at iteration {iterations}")
                return current_response, evaluation, iterations

            # Check for improvement stagnation
            if i > 0 and evaluation.score <= last_score:
                logger.info(
                    f"No improvement detected at iteration {iterations}, "
                    f"stopping refinement"
                )
                return current_response, evaluation, iterations

            last_score = evaluation.score

            # Refine if quality still needs improvement
            if evaluation.should_refine:
                current_response = await self.refine(
                    current_response,
                    evaluation.feedback,
                    query,
                    context,
                )

        # Final evaluation after all iterations
        final_evaluation = await self.evaluate(current_response, query, context)
        logger.info(
            f"Reflection complete after {iterations} iterations: "
            f"final_score={final_evaluation.score:.2f}"
        )

        return current_response, final_evaluation, iterations

    async def evaluate(
        self,
        response: str,
        query: str,
        context: Optional[dict] = None,
    ) -> EvaluationResult:
        """
        Evaluate a response using the configured evaluator.

        Args:
            response: Response to evaluate
            query: Original query
            context: Optional context

        Returns:
            EvaluationResult with quality assessment
        """
        return await self.evaluator.evaluate(response, query, context)

    async def refine(
        self,
        response: str,
        feedback: str,
        query: str,
        context: Optional[dict] = None,
    ) -> str:
        """
        Refine a response using the configured refiner.

        Args:
            response: Response to refine
            feedback: Feedback for improvement
            query: Original query
            context: Optional context

        Returns:
            Refined response
        """
        return await self.refiner.refine(response, feedback, query, context)
