"""
Evaluation Service (LLM-as-a-Judge).

Provides semantic evaluation of agent responses using DeepEval.
This service wraps DeepEval metrics and providing a simpler async interface
for integrating into the baselithcore pipeline or running offline benchmarks.
"""

from core.observability.logging import get_logger
from typing import Dict, Any, List, Optional, Type
import os
import asyncio

from core.config import get_llm_config

logger = get_logger(__name__)

# Lazy import of deepeval (optional dependency)
DEEPEVAL_AVAILABLE = False
_AnswerRelevancyMetric: Optional[Type[Any]] = None
_FaithfulnessMetric: Optional[Type[Any]] = None
_ContextualPrecisionMetric: Optional[Type[Any]] = None
_ContextualRecallMetric: Optional[Type[Any]] = None
_LLMTestCase: Optional[Type[Any]] = None

try:
    from deepeval.metrics import (
        AnswerRelevancyMetric as _ARM,
        FaithfulnessMetric as _FM,
        ContextualPrecisionMetric as _CPM,
        ContextualRecallMetric as _CRM,
    )
    from deepeval.test_case import LLMTestCase as _LTC

    _AnswerRelevancyMetric = _ARM
    _FaithfulnessMetric = _FM
    _ContextualPrecisionMetric = _CPM
    _ContextualRecallMetric = _CRM
    _LLMTestCase = _LTC
    DEEPEVAL_AVAILABLE = True
except ImportError:
    logger.warning(
        "deepeval not installed. Evaluation features will be disabled. "
        "Install with: pip install deepeval"
    )


class EvaluationService:
    """
    Service for evaluating RAG and Agent responses using LLM-as-a-Judge.
    """

    def __init__(self, use_openai: bool = True):
        """
        Initialize the evaluation service.

        Args:
            use_openai: Whether to use OpenAI for evaluation (preferred for DeepEval).
        """
        self.config = get_llm_config()
        self._available = DEEPEVAL_AVAILABLE

        if not DEEPEVAL_AVAILABLE:
            logger.warning(
                "EvaluationService initialized but deepeval is not available."
            )
            self.relevancy_metric: Any = None
            self.faithfulness_metric: Any = None
            self.precision_metric: Any = None
            self.recall_metric: Any = None
            return

        # DeepEval relies on environment variables for OpenAI
        # We ensure they are set from our config
        if use_openai and self.config.provider == "openai":
            if self.config.api_key:
                os.environ["OPENAI_API_KEY"] = self.config.api_key

        # Initialize metrics (lazy loading could be better but metrics are light)
        # We use default thresholds
        assert _AnswerRelevancyMetric is not None  # nosec B101
        assert _FaithfulnessMetric is not None  # nosec B101
        assert _ContextualPrecisionMetric is not None  # nosec B101
        assert _ContextualRecallMetric is not None  # nosec B101

        self.relevancy_metric = _AnswerRelevancyMetric(threshold=0.7)
        self.faithfulness_metric = _FaithfulnessMetric(threshold=0.7)
        # RAG Metrics
        self.precision_metric = _ContextualPrecisionMetric(threshold=0.7)
        self.recall_metric = _ContextualRecallMetric(threshold=0.7)

    async def evaluate_rag_response(
        self,
        query: str,
        response: str,
        retrieved_context: List[str],
        expected_output: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate a RAG response for Hallucination and Relevancy.

        Uses LLM-as-a-Judge (via DeepEval) to assess faithfulness, answer
        relevancy, and retrieval quality in parallel using a thread pool.

        Args:
            query: The original user question.
            response: The generated answer from the LLM.
            retrieved_context: The retrieved document chunks used for generation.
            expected_output: Gold standard reference (optional).

        Returns:
            Dict[str, Any]: Mapping of metric scores and detailed reasoning.
        """
        if not self._available or _LLMTestCase is None:
            return {
                "error": "deepeval not installed",
                "faithfulness": {"error": "deepeval not installed"},
                "answer_relevancy": {"error": "deepeval not installed"},
            }

        test_case = _LLMTestCase(
            input=query,
            actual_output=response,
            retrieval_context=retrieved_context,
            expected_output=expected_output,
        )

        results: Dict[str, Any] = {}
        loop = asyncio.get_running_loop()

        try:
            # 1. Faithfulness (e.g. "Is the answer derived from context?")
            # Execute in thread pool
            await loop.run_in_executor(
                None, self.faithfulness_metric.measure, test_case
            )
            results["faithfulness"] = {
                "score": self.faithfulness_metric.score,
                "reason": self.faithfulness_metric.reason,
                "passed": self.faithfulness_metric.is_successful(),
            }
        except Exception as e:
            logger.warning(f"Faithfulness check failed: {e}")
            results["faithfulness"] = {"error": str(e)}

        try:
            # 2. Answer Relevancy (e.g. "Does it answer the question?")
            await loop.run_in_executor(None, self.relevancy_metric.measure, test_case)
            results["answer_relevancy"] = {
                "score": self.relevancy_metric.score,
                "reason": self.relevancy_metric.reason,
                "passed": self.relevancy_metric.is_successful(),
            }
        except Exception as e:
            logger.warning(f"Relevancy check failed: {e}")
            results["answer_relevancy"] = {"error": str(e)}

        # 3. Contextual Precision (are retrieved docs relevant and well-ordered?)
        if expected_output and self.precision_metric:
            try:
                await loop.run_in_executor(
                    None, self.precision_metric.measure, test_case
                )
                results["contextual_precision"] = {
                    "score": self.precision_metric.score,
                    "reason": self.precision_metric.reason,
                    "passed": self.precision_metric.is_successful(),
                }
            except Exception as e:
                logger.warning(f"Contextual precision check failed: {e}")
                results["contextual_precision"] = {"error": str(e)}

        # 4. Contextual Recall (did we retrieve all the relevant docs?)
        if expected_output and self.recall_metric:
            try:
                await loop.run_in_executor(None, self.recall_metric.measure, test_case)
                results["contextual_recall"] = {
                    "score": self.recall_metric.score,
                    "reason": self.recall_metric.reason,
                    "passed": self.recall_metric.is_successful(),
                }
            except Exception as e:
                logger.warning(f"Contextual recall check failed: {e}")
                results["contextual_recall"] = {"error": str(e)}

        logger.info(f"Evaluation finished. Results: {results}")
        return results


# Global instance
_eval_service: Optional[EvaluationService] = None


def get_evaluation_service() -> EvaluationService:
    """Get or create global EvaluationService."""
    global _eval_service
    if _eval_service is None:
        _eval_service = EvaluationService()
    return _eval_service
