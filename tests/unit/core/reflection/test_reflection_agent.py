"""
Tests for core.reflection.agent module.
"""

import pytest
from unittest.mock import Mock, AsyncMock

from core.reflection.agent import ReflectionAgent
from core.reflection.protocols import QualityLevel, EvaluationResult


class TestReflectionAgentInit:
    """Tests for ReflectionAgent initialization."""

    def test_init_with_defaults(self):
        """Test init with default values."""
        mock_evaluator = Mock()
        mock_refiner = Mock()

        agent = ReflectionAgent(mock_evaluator, mock_refiner)

        assert agent.evaluator == mock_evaluator
        assert agent.refiner == mock_refiner
        assert agent.max_iterations == ReflectionAgent.DEFAULT_MAX_ITERATIONS
        assert agent.quality_threshold == ReflectionAgent.DEFAULT_QUALITY_THRESHOLD

    def test_init_with_custom_values(self):
        """Test init with custom values."""
        mock_evaluator = Mock()
        mock_refiner = Mock()

        agent = ReflectionAgent(
            mock_evaluator,
            mock_refiner,
            max_iterations=5,
            quality_threshold=0.9,
        )

        assert agent.max_iterations == 5
        assert agent.quality_threshold == 0.9


@pytest.mark.asyncio
class TestReflectionAgentReflect:
    """Tests for reflect method."""

    async def test_reflect_quality_met_first_iteration(self):
        """Test reflect exits early when quality met."""
        mock_evaluator = AsyncMock()
        mock_refiner = AsyncMock()

        good_eval = EvaluationResult(
            quality=QualityLevel.GOOD,
            score=0.85,
            feedback="Good",
            should_refine=False,
        )
        mock_evaluator.evaluate.return_value = good_eval

        agent = ReflectionAgent(mock_evaluator, mock_refiner, quality_threshold=0.7)

        response, evaluation, iterations = await agent.reflect("Response", "Query")

        assert response == "Response"
        assert evaluation == good_eval
        assert iterations == 1
        mock_refiner.refine.assert_not_called()

    async def test_reflect_refines_when_needed(self):
        """Test reflect refines when quality low."""
        mock_evaluator = AsyncMock()
        mock_refiner = AsyncMock()

        low_eval = EvaluationResult(
            quality=QualityLevel.NEEDS_IMPROVEMENT,
            score=0.4,
            feedback="Needs improvement",
            should_refine=True,
        )
        good_eval = EvaluationResult(
            quality=QualityLevel.GOOD,
            score=0.85,
            feedback="Good",
            should_refine=False,
        )
        mock_evaluator.evaluate.side_effect = [low_eval, good_eval]
        mock_refiner.refine.return_value = "Improved response"

        agent = ReflectionAgent(
            mock_evaluator, mock_refiner, quality_threshold=0.7, max_iterations=3
        )

        response, evaluation, iterations = await agent.reflect("Original", "Query")

        assert response == "Improved response"
        assert evaluation == good_eval
        assert iterations == 2
        mock_refiner.refine.assert_called_once()

    async def test_reflect_stops_on_no_improvement(self):
        """Test reflect stops when no improvement detected."""
        mock_evaluator = AsyncMock()
        mock_refiner = AsyncMock()

        low_eval = EvaluationResult(
            quality=QualityLevel.NEEDS_IMPROVEMENT,
            score=0.4,
            feedback="Needs improvement",
            should_refine=True,
        )
        # Second eval has same or lower score
        mock_evaluator.evaluate.side_effect = [low_eval, low_eval]
        mock_refiner.refine.return_value = "Still not good"

        agent = ReflectionAgent(
            mock_evaluator, mock_refiner, quality_threshold=0.7, max_iterations=3
        )

        response, evaluation, iterations = await agent.reflect("Original", "Query")

        # Should stop after 2 iterations (no improvement)
        assert iterations == 2


@pytest.mark.asyncio
class TestReflectionAgentEvaluate:
    """Tests for evaluate method."""

    async def test_evaluate_calls_evaluator(self):
        """Test evaluate delegates to evaluator."""
        mock_evaluator = AsyncMock()
        mock_refiner = Mock()

        expected = EvaluationResult(
            quality=QualityLevel.GOOD,
            score=0.8,
            feedback="Good",
            should_refine=False,
        )
        mock_evaluator.evaluate.return_value = expected

        agent = ReflectionAgent(mock_evaluator, mock_refiner)
        result = await agent.evaluate("Response", "Query", {"key": "value"})

        assert result == expected
        mock_evaluator.evaluate.assert_called_once_with(
            "Response", "Query", {"key": "value"}
        )


@pytest.mark.asyncio
class TestReflectionAgentRefine:
    """Tests for refine method."""

    async def test_refine_calls_refiner(self):
        """Test refine delegates to refiner."""
        mock_evaluator = Mock()
        mock_refiner = AsyncMock()
        mock_refiner.refine.return_value = "Refined response"

        agent = ReflectionAgent(mock_evaluator, mock_refiner)
        result = await agent.refine("Original", "Feedback", "Query", {"key": "value"})

        assert result == "Refined response"
        mock_refiner.refine.assert_called_once_with(
            "Original", "Feedback", "Query", {"key": "value"}
        )
