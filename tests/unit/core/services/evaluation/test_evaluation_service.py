"""
Unit tests for EvaluationService.
"""

import pytest
import os
from unittest.mock import patch
from core.services.evaluation.service import EvaluationService, get_evaluation_service


@pytest.fixture
def mock_deepeval():
    with patch("core.services.evaluation.service.DEEPEVAL_AVAILABLE", True):
        with (
            patch("core.services.evaluation.service._AnswerRelevancyMetric") as m1,
            patch("core.services.evaluation.service._FaithfulnessMetric") as m2,
            patch("core.services.evaluation.service._ContextualPrecisionMetric") as m3,
            patch("core.services.evaluation.service._ContextualRecallMetric") as m4,
            patch("core.services.evaluation.service._LLMTestCase") as m5,
        ):
            yield m1, m2, m3, m4, m5


def test_eval_service_init_no_deepeval():
    """Test initialization when deepeval is unavailable."""
    with patch("core.services.evaluation.service.DEEPEVAL_AVAILABLE", False):
        service = EvaluationService()
        assert service._available is False
        assert service.relevancy_metric is None


def test_eval_service_init_with_deepeval(mock_deepeval):
    """Test initialization when deepeval is available."""
    m1, m2, m3, m4, m5 = mock_deepeval
    service = EvaluationService()
    assert service._available is True
    m1.assert_called_once()
    m2.assert_called_once()
    m3.assert_called_once()
    m4.assert_called_once()


def test_eval_service_openai_env(mock_deepeval):
    """Test setting OPENAI_API_KEY from config."""
    with patch("core.services.evaluation.service.get_llm_config") as mock_config:
        mock_config.return_value.provider = "openai"
        mock_config.return_value.api_key = "test-key"

        # Clear env first
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]

        _ = EvaluationService(use_openai=True)
        assert os.environ.get("OPENAI_API_KEY") == "test-key"


@pytest.mark.asyncio
async def test_evaluate_rag_response_basic(mock_deepeval):
    """Test basic RAG evaluation flow."""
    m1, m2, m3, m4, m5 = mock_deepeval
    service = EvaluationService()

    # Mock metrics
    service.faithfulness_metric.score = 0.9
    service.faithfulness_metric.reason = "Faithful"
    service.faithfulness_metric.is_successful.return_value = True

    service.relevancy_metric.score = 0.8
    service.relevancy_metric.reason = "Relevant"
    service.relevancy_metric.is_successful.return_value = True

    results = await service.evaluate_rag_response(
        query="q", response="r", retrieved_context=["c"]
    )

    assert results["faithfulness"]["score"] == 0.9
    assert results["answer_relevancy"]["score"] == 0.8


@pytest.mark.asyncio
async def test_evaluate_rag_response_with_expected(mock_deepeval):
    """Test evaluation with expected output (precision/recall)."""
    m1, m2, m3, m4, m5 = mock_deepeval
    service = EvaluationService()

    service.precision_metric.score = 1.0
    service.precision_metric.reason = "Precise"
    service.precision_metric.is_successful.return_value = True

    service.recall_metric.score = 1.0
    service.recall_metric.reason = "Full recall"
    service.recall_metric.is_successful.return_value = True

    results = await service.evaluate_rag_response(
        query="q", response="r", retrieved_context=["c"], expected_output="gold"
    )

    assert "contextual_precision" in results
    assert "contextual_recall" in results
    assert results["contextual_precision"]["score"] == 1.0


@pytest.mark.asyncio
async def test_evaluate_rag_response_errors(mock_deepeval):
    """Test error handling in individual metrics."""
    m1, m2, m3, m4, m5 = mock_deepeval
    service = EvaluationService()

    # Force error in faithfulness
    service.faithfulness_metric.measure.side_effect = Exception("Metric failed")

    results = await service.evaluate_rag_response(
        query="q", response="r", retrieved_context=["c"]
    )

    assert "error" in results["faithfulness"]
    assert results["faithfulness"]["error"] == "Metric failed"


@pytest.mark.asyncio
async def test_evaluate_rag_response_unavailable(mock_deepeval):
    """Test evaluation returns error when service unavailable."""
    service = EvaluationService()
    service._available = False

    results = await service.evaluate_rag_response("q", "r", ["c"])
    assert "error" in results
    assert results["error"] == "deepeval not installed"


def test_get_evaluation_service_global(mock_deepeval):
    """Test global instance retrieval."""
    with patch("core.services.evaluation.service._eval_service", None):
        inst = get_evaluation_service()
        assert isinstance(inst, EvaluationService)
        inst2 = get_evaluation_service()
        assert inst is inst2


@pytest.mark.asyncio
async def test_evaluate_rag_response_empty_context(mock_deepeval):
    """Test RAG evaluation with empty retrieved context."""
    m1, m2, m3, m4, m5 = mock_deepeval
    service = EvaluationService()

    # Mock metrics
    service.faithfulness_metric.score = 0.0
    service.faithfulness_metric.reason = "No context"
    service.faithfulness_metric.is_successful.return_value = False

    results = await service.evaluate_rag_response(
        query="q", response="r", retrieved_context=[]
    )

    assert results["faithfulness"]["score"] == 0.0
    assert "error" not in results
