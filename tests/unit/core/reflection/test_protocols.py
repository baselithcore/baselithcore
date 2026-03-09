"""
Tests for core.reflection.protocols module.
"""

from core.reflection.protocols import (
    QualityLevel,
    EvaluationResult,
    SelfEvaluator,
    Refiner,
)


class TestQualityLevel:
    """Tests for QualityLevel enum."""

    def test_quality_levels(self):
        """Test all quality levels exist."""
        assert QualityLevel.EXCELLENT.value == "excellent"
        assert QualityLevel.GOOD.value == "good"
        assert QualityLevel.ACCEPTABLE.value == "acceptable"
        assert QualityLevel.NEEDS_IMPROVEMENT.value == "needs_improvement"
        assert QualityLevel.POOR.value == "poor"

    def test_quality_level_count(self):
        """Test there are 5 quality levels."""
        assert len(QualityLevel) == 5


class TestEvaluationResult:
    """Tests for EvaluationResult dataclass."""

    def test_create_evaluation_result(self):
        """Test creating an EvaluationResult."""
        result = EvaluationResult(
            quality=QualityLevel.GOOD,
            score=0.85,
            feedback="Good response",
            should_refine=False,
        )

        assert result.quality == QualityLevel.GOOD
        assert result.score == 0.85
        assert result.feedback == "Good response"
        assert result.should_refine is False
        assert result.aspects == {}

    def test_with_aspects(self):
        """Test EvaluationResult with aspects."""
        result = EvaluationResult(
            quality=QualityLevel.ACCEPTABLE,
            score=0.7,
            feedback="OK",
            should_refine=True,
            aspects={"clarity": 0.8, "accuracy": 0.6},
        )

        assert result.aspects["clarity"] == 0.8
        assert result.aspects["accuracy"] == 0.6

    def test_is_acceptable_excellent(self):
        """Test is_acceptable for EXCELLENT."""
        result = EvaluationResult(
            quality=QualityLevel.EXCELLENT,
            score=0.95,
            feedback="",
            should_refine=False,
        )

        assert result.is_acceptable is True

    def test_is_acceptable_good(self):
        """Test is_acceptable for GOOD."""
        result = EvaluationResult(
            quality=QualityLevel.GOOD,
            score=0.8,
            feedback="",
            should_refine=False,
        )

        assert result.is_acceptable is True

    def test_is_acceptable_acceptable(self):
        """Test is_acceptable for ACCEPTABLE."""
        result = EvaluationResult(
            quality=QualityLevel.ACCEPTABLE,
            score=0.7,
            feedback="",
            should_refine=False,
        )

        assert result.is_acceptable is True

    def test_is_acceptable_needs_improvement(self):
        """Test is_acceptable for NEEDS_IMPROVEMENT."""
        result = EvaluationResult(
            quality=QualityLevel.NEEDS_IMPROVEMENT,
            score=0.5,
            feedback="Needs work",
            should_refine=True,
        )

        assert result.is_acceptable is False

    def test_is_acceptable_poor(self):
        """Test is_acceptable for POOR."""
        result = EvaluationResult(
            quality=QualityLevel.POOR,
            score=0.2,
            feedback="Poor",
            should_refine=True,
        )

        assert result.is_acceptable is False


class TestProtocols:
    """Tests for Protocol classes."""

    def test_self_evaluator_is_protocol(self):
        """Test SelfEvaluator is a Protocol."""
        # Just check it's importable and usable as type hint
        assert SelfEvaluator is not None

    def test_refiner_is_protocol(self):
        """Test Refiner is a Protocol."""
        assert Refiner is not None
