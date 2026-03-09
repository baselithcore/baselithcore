"""
Evaluation metrics wrapper using DeepEval.
Controlled by EvaluationConfig to prevent accidental usage.
"""

from typing import List, Optional
from core.config.evaluation import evaluation_config

from core.observability.logging import get_logger
from dataclasses import dataclass

logger = get_logger(__name__)


# Dummy classes to avoid import errors if deepeval is not installed or config is disabled
@dataclass
class MetricResult:
    """Result of a metric evaluation."""

    score: float
    reason: Optional[str] = None
    metadata: Optional[dict] = None


class BaseMetricWrapper:
    """Base class for wrapping metrics with evaluation logic."""

    def __init__(self, metric_name: str, threshold: float = 0.5):
        """
        Initialize base metric wrapper.

        Args:
            metric_name: Human-readable name of the metric.
            threshold: Success threshold (0.0 to 1.0).
        """
        self.metric = None
        self.metric_name = metric_name
        self.threshold = threshold

    async def measure(self, input_data: dict) -> MetricResult:
        """
        Perform the metric measurement.

        Args:
            input_data: The data to evaluate.

        Returns:
            A MetricResult containing the score and optional metadata.
        """
        return MetricResult(score=1.0, reason="Evaluation disabled or not implemented.")

    def is_successful(self, result: MetricResult) -> bool:
        """
        Check if the metric result meets the success threshold.

        Args:
            result: The measurement result to check.

        Returns:
            True if successful, False otherwise.
        """
        return result.score >= self.threshold


# Attempt to import deepeval if enabled
DEEPEVAL_AVAILABLE = False
try:
    if evaluation_config.is_enabled:
        from deepeval.metrics import (
            FaithfulnessMetric,
            AnswerRelevancyMetric,
        )
        from deepeval.test_case import LLMTestCase

        DEEPEVAL_AVAILABLE = True
except ImportError:
    logger.warning("DeepEval not installed. Evaluation capabilities disabled.")


class FaithfulnessEvaluator(BaseMetricWrapper):
    """Evaluates the faithfulness of a response to its source context."""

    def __init__(self, threshold: float = 0.7):
        """
        Initialize faithfulness evaluator.

        Args:
            threshold: Minimum score for success. Defaults to 0.7.
        """
        if DEEPEVAL_AVAILABLE:
            self.metric = FaithfulnessMetric(
                threshold=threshold, model=evaluation_config.model, include_reason=True
            )
        else:
            self.metric = None

    def measure(  # type: ignore[override]
        self, input_text: str, actual_output: str, retrieval_context: List[str]
    ) -> float:
        """
        Measure the faithfulness of the response against the retrieval context.

        Args:
            input_text: The original user query.
            actual_output: The model's generated response.
            retrieval_context: List of context strings retrieved for RAG.

        Returns:
            A score between 0.0 and 1.0.
        """
        if not self.metric:
            logger.info(
                "Skipping Faithfulness evaluation (disabled or missing dependency)."
            )
            return 0.0

        test_case = LLMTestCase(
            input=input_text,
            actual_output=actual_output,
            retrieval_context=retrieval_context,
        )
        try:
            self.metric.measure(test_case)
            logger.info(
                f"Faithfulness Score: {self.metric.score} - {self.metric.reason}"
            )
            return self.metric.score
        except Exception as e:
            logger.error(f"Error checking faithfulness: {e}")
            return 0.0


class AnswerRelevancyEvaluator(BaseMetricWrapper):
    """Evaluates how relevant an answer is to the original user query."""

    def __init__(self, threshold: float = 0.7):
        """
        Initialize answer relevancy evaluator.

        Args:
            threshold: Minimum score for success. Defaults to 0.7.
        """
        if DEEPEVAL_AVAILABLE:
            self.metric = AnswerRelevancyMetric(
                threshold=threshold, model=evaluation_config.model, include_reason=True
            )
        else:
            self.metric = None

    def measure(self, input_text: str, actual_output: str) -> float:  # type: ignore[override]
        """
        Measure how relevant the generated answer is to the input query.

        Args:
            input_text: The original user query.
            actual_output: The model's generated response.

        Returns:
            A score between 0.0 and 1.0.
        """
        if not self.metric:
            logger.info("Skipping Answer Relevancy evaluation.")
            return 0.0

        test_case = LLMTestCase(input=input_text, actual_output=actual_output)
        try:
            self.metric.measure(test_case)
            logger.info(
                f"Answer Relevancy Score: {self.metric.score} - {self.metric.reason}"
            )
            return self.metric.score
        except Exception as e:
            logger.error(f"Error checking answer relevancy: {e}")
            return 0.0
