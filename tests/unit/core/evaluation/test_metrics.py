import pytest
from unittest.mock import MagicMock, patch
from core.evaluation.metrics import (
    FaithfulnessEvaluator,
    AnswerRelevancyEvaluator,
    BaseMetricWrapper,
)


class TestEvaluationMetrics:
    @pytest.mark.asyncio
    async def test_base_metric_wrapper(self):
        wrapper = BaseMetricWrapper(metric_name="test")
        result = await wrapper.measure("test")
        assert result.score == 1.0
        assert wrapper.is_successful(result) is True

    @patch("core.evaluation.metrics.DEEPEVAL_AVAILABLE", False)
    def test_evaluators_when_deepeval_unavailable(self):
        # Faithfulness
        f_eval = FaithfulnessEvaluator()
        assert f_eval.metric is None
        assert f_eval.measure("q", "a", ["c"]) == 0.0

        # Answer Relevancy
        r_eval = AnswerRelevancyEvaluator()
        assert r_eval.metric is None
        assert r_eval.measure("q", "a") == 0.0

    @patch("core.evaluation.metrics.DEEPEVAL_AVAILABLE", True)
    @patch("core.evaluation.metrics.FaithfulnessMetric", create=True)
    @patch("core.evaluation.metrics.LLMTestCase", create=True)
    def test_faithfulness_evaluator_measure(self, mock_test_case, mock_metric_class):
        mock_metric_instance = MagicMock()
        mock_metric_instance.score = 0.85
        mock_metric_instance.reason = "Good"
        mock_metric_class.return_value = mock_metric_instance

        evaluator = FaithfulnessEvaluator()
        score = evaluator.measure("query", "output", ["context"])

        assert score == 0.85
        mock_metric_instance.measure.assert_called_once()

    @patch("core.evaluation.metrics.DEEPEVAL_AVAILABLE", True)
    @patch("core.evaluation.metrics.AnswerRelevancyMetric", create=True)
    @patch("core.evaluation.metrics.LLMTestCase", create=True)
    def test_answer_relevancy_evaluator_measure(
        self, mock_test_case, mock_metric_class
    ):
        mock_metric_instance = MagicMock()
        mock_metric_instance.score = 0.92
        mock_metric_instance.reason = "Relevant"
        mock_metric_class.return_value = mock_metric_instance

        evaluator = AnswerRelevancyEvaluator()
        score = evaluator.measure("query", "output")

        assert score == 0.92
        mock_metric_instance.measure.assert_called_once()

    @patch("core.evaluation.metrics.DEEPEVAL_AVAILABLE", True)
    @patch("core.evaluation.metrics.FaithfulnessMetric", create=True)
    @patch("core.evaluation.metrics.LLMTestCase", create=True)
    def test_evaluator_exception_handling(
        self, mock_test_case_class, mock_metric_class
    ):
        mock_metric_instance = MagicMock()
        mock_metric_instance.measure.side_effect = Exception("Eval error")
        mock_metric_class.return_value = mock_metric_instance

        evaluator = FaithfulnessEvaluator()
        # Should catch exception and return 0.0
        score = evaluator.measure("q", "a", ["c"])
        assert score == 0.0
