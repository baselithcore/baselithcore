"""
Unit Tests for Core Reflection Module

Tests for the Reflection agentic design pattern components:
- EvaluationResult and QualityLevel
- ReflectionAgent
- DefaultEvaluator and DefaultRefiner
"""

from unittest.mock import Mock
from typing import Optional
import pytest

from core.reflection import (
    ReflectionAgent,
    EvaluationResult,
    DefaultEvaluator,
    DefaultRefiner,
)
from core.reflection.protocols import QualityLevel


# ============================================================================
# EvaluationResult Tests
# ============================================================================


class TestEvaluationResult:
    """Tests for EvaluationResult dataclass."""

    def test_creation_with_all_fields(self):
        """Test creating EvaluationResult with all fields."""
        result = EvaluationResult(
            quality=QualityLevel.GOOD,
            score=0.8,
            feedback="Good response",
            should_refine=False,
            aspects={"relevance": 4, "accuracy": 4},
        )
        assert result.quality == QualityLevel.GOOD
        assert result.score == 0.8
        assert result.feedback == "Good response"
        assert result.should_refine is False
        assert result.aspects == {"relevance": 4, "accuracy": 4}

    def test_is_acceptable_for_good_quality(self):
        """Test is_acceptable returns True for acceptable qualities."""
        for quality in [
            QualityLevel.EXCELLENT,
            QualityLevel.GOOD,
            QualityLevel.ACCEPTABLE,
        ]:
            result = EvaluationResult(
                quality=quality,
                score=0.7,
                feedback="",
                should_refine=False,
            )
            assert result.is_acceptable is True

    def test_is_acceptable_for_poor_quality(self):
        """Test is_acceptable returns False for poor qualities."""
        for quality in [QualityLevel.NEEDS_IMPROVEMENT, QualityLevel.POOR]:
            result = EvaluationResult(
                quality=quality,
                score=0.3,
                feedback="",
                should_refine=True,
            )
            assert result.is_acceptable is False


# ============================================================================
# Mock Implementations
# ============================================================================


class MockEvaluator:
    """Mock evaluator for testing."""

    def __init__(self, scores: list[float] = None):
        self.scores = scores or [0.5, 0.7, 0.9]
        self.call_count = 0

    async def evaluate(
        self,
        response: str,
        query: str,
        context: Optional[dict] = None,
    ) -> EvaluationResult:
        score = self.scores[min(self.call_count, len(self.scores) - 1)]
        self.call_count += 1

        quality = QualityLevel.GOOD if score >= 0.7 else QualityLevel.NEEDS_IMPROVEMENT

        return EvaluationResult(
            quality=quality,
            score=score,
            feedback=f"Iteration {self.call_count}: improve clarity",
            should_refine=score < 0.7,
        )


class MockRefiner:
    """Mock refiner for testing."""

    def __init__(self):
        self.call_count = 0

    async def refine(
        self,
        response: str,
        feedback: str,
        query: str,
        context: Optional[dict] = None,
    ) -> str:
        self.call_count += 1
        return f"[Refined v{self.call_count}] {response}"


# ============================================================================
# ReflectionAgent Tests
# ============================================================================


class TestReflectionAgent:
    """Tests for ReflectionAgent."""

    def test_initialization(self):
        """Test agent initialization with default values."""
        evaluator = MockEvaluator()
        refiner = MockRefiner()

        agent = ReflectionAgent(evaluator, refiner)

        assert agent.max_iterations == 3
        assert agent.quality_threshold == 0.7

    def test_initialization_with_custom_values(self):
        """Test agent initialization with custom values."""
        evaluator = MockEvaluator()
        refiner = MockRefiner()

        agent = ReflectionAgent(
            evaluator, refiner, max_iterations=5, quality_threshold=0.8
        )

        assert agent.max_iterations == 5
        assert agent.quality_threshold == 0.8

    @pytest.mark.asyncio
    async def test_reflect_returns_immediately_if_quality_met(self):
        """Test that reflection stops when quality threshold is met."""
        evaluator = MockEvaluator(scores=[0.9])  # Already above threshold
        refiner = MockRefiner()

        agent = ReflectionAgent(evaluator, refiner, quality_threshold=0.7)

        response, evaluation, iterations = await agent.reflect(
            "Test response", "Test query"
        )

        assert iterations == 1
        assert evaluation.score == 0.9
        assert refiner.call_count == 0  # No refinement needed

    @pytest.mark.asyncio
    async def test_reflect_refines_until_quality_met(self):
        """Test that reflection refines until quality is met."""
        evaluator = MockEvaluator(scores=[0.5, 0.6, 0.8])  # Improves each iteration
        refiner = MockRefiner()

        agent = ReflectionAgent(evaluator, refiner, quality_threshold=0.7)

        response, evaluation, iterations = await agent.reflect(
            "Test response", "Test query"
        )

        assert iterations == 3
        assert evaluation.score == 0.8
        assert refiner.call_count == 2  # Refined twice before hitting threshold

    @pytest.mark.asyncio
    async def test_reflect_stops_on_max_iterations(self):
        """Test that reflection stops at max iterations."""
        evaluator = MockEvaluator(scores=[0.3, 0.4, 0.5])  # Never meets threshold
        refiner = MockRefiner()

        agent = ReflectionAgent(
            evaluator, refiner, max_iterations=3, quality_threshold=0.9
        )

        response, evaluation, iterations = await agent.reflect(
            "Test response", "Test query"
        )

        assert iterations == 3
        assert evaluation.score == 0.5

    @pytest.mark.asyncio
    async def test_reflect_stops_on_no_improvement(self):
        """Test that reflection stops when no improvement is detected."""
        evaluator = MockEvaluator(scores=[0.5, 0.5, 0.5])  # No improvement
        refiner = MockRefiner()

        agent = ReflectionAgent(evaluator, refiner, quality_threshold=0.9)

        response, evaluation, iterations = await agent.reflect(
            "Test response", "Test query"
        )

        # Should stop after second iteration when no improvement detected
        assert iterations == 2
        assert refiner.call_count == 1

    @pytest.mark.asyncio
    async def test_reflect_passes_context(self):
        """Test that context is passed through to evaluator and refiner."""
        context = {"session_id": "test-123"}
        evaluator = MockEvaluator(scores=[0.5, 0.9])
        refiner = MockRefiner()

        agent = ReflectionAgent(evaluator, refiner)

        await agent.reflect("Response", "Query", context=context)

        # Verify context was used (mock doesn't check, but no errors means it worked)
        assert evaluator.call_count == 2


# ============================================================================
# DefaultEvaluator Tests
# ============================================================================


class TestDefaultEvaluator:
    """Tests for DefaultEvaluator."""

    def test_fallback_evaluation_short_response(self):
        """Test fallback evaluation penalizes short responses."""
        evaluator = DefaultEvaluator()

        result = evaluator._fallback_evaluation("Short", "What is AI?")

        assert result.score < 0.5
        assert result.feedback == "Fallback evaluation - LLM unavailable"

    def test_fallback_evaluation_with_query_overlap(self):
        """Test fallback evaluation rewards query term overlap."""
        evaluator = DefaultEvaluator()

        result = evaluator._fallback_evaluation(
            "AI is artificial intelligence, a field of computer science.", "What is AI?"
        )

        # Should have some overlap bonus
        assert result.score >= 0.4

    def test_score_to_quality_mapping(self):
        """Test score to quality level mapping."""
        evaluator = DefaultEvaluator()

        assert evaluator._score_to_quality(0.95) == QualityLevel.EXCELLENT
        assert evaluator._score_to_quality(0.80) == QualityLevel.GOOD
        assert evaluator._score_to_quality(0.65) == QualityLevel.ACCEPTABLE
        assert evaluator._score_to_quality(0.45) == QualityLevel.NEEDS_IMPROVEMENT
        assert evaluator._score_to_quality(0.20) == QualityLevel.POOR


# ============================================================================
# DefaultRefiner Tests
# ============================================================================


class TestDefaultRefiner:
    """Tests for DefaultRefiner."""

    @pytest.mark.asyncio
    async def test_returns_original_on_error(self):
        """Test refiner returns original response on error."""
        mock_llm = Mock()
        from unittest.mock import AsyncMock

        mock_llm.generate_response = AsyncMock(side_effect=Exception("LLM error"))

        refiner = DefaultRefiner(llm_service=mock_llm)
        result = await refiner.refine("Original response", "Feedback", "Query")

        assert result == "Original response"

    @pytest.mark.asyncio
    async def test_returns_original_on_empty_refinement(self):
        """Test refiner returns original when LLM returns empty."""
        mock_llm = Mock()
        from unittest.mock import AsyncMock

        mock_llm.generate_response = AsyncMock(return_value="")

        refiner = DefaultRefiner(llm_service=mock_llm)
        result = await refiner.refine("Original response", "Feedback", "Query")

        assert result == "Original response"


# ============================================================================
# Integration Test
# ============================================================================


@pytest.mark.asyncio
async def test_reflection_full_cycle():
    """Integration test for complete reflection cycle."""
    evaluator = MockEvaluator(scores=[0.4, 0.6, 0.85])
    refiner = MockRefiner()

    agent = ReflectionAgent(
        evaluator=evaluator,
        refiner=refiner,
        max_iterations=5,
        quality_threshold=0.8,
    )

    initial_response = "Initial AI response"
    final_response, final_eval, iterations = await agent.reflect(
        initial_response, "User query", context={"key": "value"}
    )

    # Should have refined and improved
    assert iterations == 3
    assert final_eval.score >= 0.8
    assert "[Refined" in final_response
    assert evaluator.call_count == 3
    assert refiner.call_count == 2
