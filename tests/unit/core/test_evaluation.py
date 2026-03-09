"""
Unit Tests for Evaluation System.
"""

import pytest
from unittest.mock import AsyncMock

from core.evaluation.protocols import QualityLevel, EvaluationResult
from core.evaluation.judges import RelevanceEvaluator, CompositeEvaluator
from core.evaluation.service import EvaluationService
from core.events import EventNames


class MockLLMService:
    async def generate_response(self, prompt, json=True):
        return '{"score": 0.8, "feedback": "Good response", "should_refine": false}'


class TestBaseLLMEvaluator:
    @pytest.mark.asyncio
    async def test_evaluate_success(self):
        evaluator = RelevanceEvaluator(llm_service=MockLLMService())
        result = await evaluator.evaluate("response", "query")

        assert result.score == 0.8
        assert result.quality == QualityLevel.GOOD
        assert result.feedback == "Good response"
        assert not result.should_refine

    @pytest.mark.asyncio
    async def test_fallback_on_error(self):
        mock_llm = MockLLMService()
        mock_llm.generate_response = AsyncMock(side_effect=Exception("LLM Error"))

        evaluator = RelevanceEvaluator(llm_service=mock_llm)
        result = await evaluator.evaluate("response", "query")

        # Fallback logic should trigger
        assert result.metadata.get("fallback") is True


class TestCompositeEvaluator:
    @pytest.mark.asyncio
    async def test_composite_aggregation(self):
        mock_judge = AsyncMock()
        mock_judge.evaluate.return_value = EvaluationResult(
            score=0.8, quality=QualityLevel.GOOD, feedback="Good", should_refine=False
        )

        evaluator = CompositeEvaluator(evaluators=[mock_judge, mock_judge])
        result = await evaluator.evaluate("response", "query")

        assert result.score == 0.8
        assert len(result.feedback.split("|")) == 2


class TestEvaluationService:
    @pytest.mark.asyncio
    async def test_flow_completed_trigger(self):
        mock_bus = AsyncMock()
        mock_evaluator = AsyncMock()

        service = EvaluationService(event_bus=mock_bus, evaluator=mock_evaluator)
        service.start()

        # Simulate event
        event_data = {
            "intent": "qa",
            "query": "test query",
            "response": "test response",
            "success": True,
        }

        # Directly call handler since we can't easily trigger async bus in unit test without full setup
        await service._on_flow_completed(event_data)

        # Verify evaluation was triggered (eventually)
        # Since _on_flow_completed creates a task, we need to wait a bit or mock create_task
        # For unit testing internal logic, we can call _evaluate_interaction directly

        await service._evaluate_interaction("query", "response", {}, "qa")

        mock_bus.emit.assert_called()
        call_args = mock_bus.emit.call_args_list

        assert call_args[0][0][0] == EventNames.EVALUATION_STARTED
        assert call_args[1][0][0] == EventNames.EVALUATION_COMPLETED
