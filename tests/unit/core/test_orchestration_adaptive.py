"""
Unit Tests for Adaptive Control (SwiftSage Pattern).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from core.orchestration.adaptive import (
    AdaptiveController,
    ProcessingPath,
    AdaptiveConfig,
)


class TestAdaptiveController:
    """Tests for AdaptiveController class."""

    @pytest.fixture
    def mock_llm(self):
        return MagicMock()

    def test_complexity_analysis_simple(self):
        """Test analyzing simple queries."""
        controller = AdaptiveController()
        signals = controller._analyze_complexity("What time is it?")

        assert signals.word_count == 4
        assert not signals.has_technical_terms
        assert not signals.requires_reasoning
        assert signals.complexity_score < 0.5

    def test_complexity_analysis_complex(self):
        """Test analyzing complex queries."""
        controller = AdaptiveController()
        # Make query complex with multi-step indicator
        signals = controller._analyze_complexity(
            "Explain how to implement optimization followed by a performance evaluation."
        )

        assert signals.has_technical_terms  # 'implement', 'optimization', 'performance'
        assert signals.has_multi_step  # 'followed by'
        assert signals.complexity_score > 0.5

    @pytest.mark.asyncio
    async def test_route_fast(self):
        """Test routing to fast path."""
        controller = AdaptiveController(config=AdaptiveConfig(fast_threshold=0.5))
        path = await controller.route("Hello world")
        assert path == ProcessingPath.FAST

    @pytest.mark.asyncio
    async def test_route_slow(self):
        """Test routing to slow path."""
        controller = AdaptiveController(config=AdaptiveConfig(slow_threshold=0.3))
        # Force high complexity
        query = (
            "Explain elaborate architectural design decisions regarding optimization"
        )
        path = await controller.route(query)
        assert path == ProcessingPath.SLOW

    @pytest.mark.asyncio
    async def test_route_with_fallback_success(self):
        """Test fast path success."""
        controller = AdaptiveController()

        fast_mock = AsyncMock(return_value="Good answer")
        slow_mock = AsyncMock(return_value="Detailed answer")

        result, path = await controller.route_with_fallback(
            "Simple query", fast_mock, slow_mock
        )

        assert result == "Good answer"
        assert path == ProcessingPath.FAST
        slow_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_with_fallback_failure(self):
        """Test fallback when fast path fails."""
        controller = AdaptiveController()

        # Fast path raises error
        fast_mock = AsyncMock(side_effect=ValueError("Fail"))
        slow_mock = AsyncMock(return_value="Detailed answer")

        result, path = await controller.route_with_fallback(
            "Simple query", fast_mock, slow_mock
        )

        assert result == "Detailed answer"
        assert path == ProcessingPath.SLOW
        fast_mock.assert_called_once()
        slow_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_route_with_fallback_inadequate(self):
        """Test fallback when fast path returns inadequate answer."""
        controller = AdaptiveController()

        # Fast path returns short/bad answer
        fast_mock = AsyncMock(return_value="IDK")
        slow_mock = AsyncMock(return_value="Detailed answer")

        result, path = await controller.route_with_fallback(
            "Simple query", fast_mock, slow_mock
        )

        assert result == "Detailed answer"
        assert path == ProcessingPath.SLOW
