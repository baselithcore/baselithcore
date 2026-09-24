"""
Evaluation metrics wrapper using DeepEval.
Controlled by EvaluationConfig to prevent accidental usage.
"""

from dataclasses import dataclass
from typing import Any

from core.config.evaluation import evaluation_config
from core.observability.logging import get_logger

logger = get_logger(__name__)


# Dummy classes to avoid import errors if deepeval is not installed or config is disabled
@dataclass
class MetricResult:
    """Result of a metric evaluation."""

    score: float
    reason: str | None = None
    metadata: dict | None = None


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


# DeepEval is resolved lazily, when an evaluator is built — not at import. The
# decision used to be taken here from ``evaluation_config.is_enabled``, which
# defaults to False, so a caller that imported this module and *then* enabled
# evaluation (``scripts/run_eval.py`` does exactly that) got evaluators with no
# metric: every case logged "Skipping" and scored 0.0, indistinguishable from
# an unfaithful answer. The module-level names stay because tests patch them.
DEEPEVAL_AVAILABLE = False
FaithfulnessMetric: Any = None
AnswerRelevancyMetric: Any = None
LLMTestCase: Any = None


def _ensure_deepeval() -> bool:
    """Import DeepEval if evaluation is enabled now; report whether it is usable.

    Returns:
        ``True`` once the DeepEval classes are bound at module level. ``False``
        while evaluation is disabled (nothing is imported — the opt-in holds)
        or when the dependency is missing.
    """
    global DEEPEVAL_AVAILABLE, FaithfulnessMetric, AnswerRelevancyMetric, LLMTestCase
    if DEEPEVAL_AVAILABLE:
        return True
    if not evaluation_config.is_enabled:
        return False
    try:
        from deepeval.metrics import AnswerRelevancyMetric as _AnswerRelevancy
        from deepeval.metrics import FaithfulnessMetric as _Faithfulness
        from deepeval.test_case import LLMTestCase as _TestCase
    except ImportError:
        logger.warning("DeepEval not installed. Evaluation capabilities disabled.")
        return False
    FaithfulnessMetric = _Faithfulness
    AnswerRelevancyMetric = _AnswerRelevancy
    LLMTestCase = _TestCase
    DEEPEVAL_AVAILABLE = True
    return True


class FaithfulnessEvaluator(BaseMetricWrapper):
    """Evaluates the faithfulness of a response to its source context."""

    def __init__(self, threshold: float = 0.7):
        """
        Initialize faithfulness evaluator.

        Args:
            threshold: Minimum score for success. Defaults to 0.7.
        """
        # Base init sets metric_name/threshold — skipping it left
        # ``is_successful`` raising AttributeError on both subclasses.
        super().__init__("faithfulness", threshold)
        if _ensure_deepeval():
            self.metric = FaithfulnessMetric(
                threshold=threshold, model=evaluation_config.model, include_reason=True
            )

    def measure(  # type: ignore[override]
        self, input_text: str, actual_output: str, retrieval_context: list[str]
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
        super().__init__("answer_relevancy", threshold)
        if _ensure_deepeval():
            self.metric = AnswerRelevancyMetric(
                threshold=threshold, model=evaluation_config.model, include_reason=True
            )

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
