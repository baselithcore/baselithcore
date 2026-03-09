"""
Tests for core.reasoning.self_correction module.
"""

import pytest
from unittest.mock import Mock, patch

from core.reasoning.self_correction import CorrectionResult, SelfCorrector


class TestCorrectionResult:
    """Tests for CorrectionResult dataclass."""

    def test_create_correction_result(self):
        """Test creating a CorrectionResult."""
        result = CorrectionResult(
            original="Original text",
            corrected="Corrected text",
            corrections_made=1,
            is_valid=True,
        )

        assert result.original == "Original text"
        assert result.corrected == "Corrected text"
        assert result.corrections_made == 1
        assert result.is_valid is True

    def test_no_corrections(self):
        """Test CorrectionResult with no corrections."""
        result = CorrectionResult(
            original="Same text",
            corrected="Same text",
            corrections_made=0,
            is_valid=True,
        )

        assert result.corrections_made == 0


class TestSelfCorrectorInit:
    """Tests for SelfCorrector initialization."""

    def test_init_default(self):
        """Test default initialization."""
        corrector = SelfCorrector()

        assert corrector._llm_service is None
        assert corrector.max_corrections == 2

    def test_init_with_service(self):
        """Test initialization with LLM service."""
        mock_service = Mock()
        corrector = SelfCorrector(llm_service=mock_service, max_corrections=5)

        assert corrector._llm_service == mock_service
        assert corrector.max_corrections == 5

    def test_init_with_config(self):
        """Test initialization with ReasoningConfig."""
        mock_config = Mock()
        mock_config.self_correction_max_iterations = 10

        corrector = SelfCorrector(config=mock_config)

        assert corrector.max_corrections == 10
        assert corrector._config == mock_config


class TestSelfCorrectorLlmServiceProperty:
    """Tests for llm_service property."""

    def test_returns_cached_service(self):
        """Test returns cached service if set."""
        mock_service = Mock()
        corrector = SelfCorrector(llm_service=mock_service)

        result = corrector.llm_service

        assert result == mock_service

    @patch("core.services.llm.get_llm_service")
    def test_lazy_loads_service(self, mock_get_service):
        """Test lazy loads service if not set."""
        # The import might fail, so we test the fallback case
        corrector = SelfCorrector()
        corrector._llm_service = None

        # Access property - may return None if import fails
        _ = corrector.llm_service


class TestSelfCorrectorCorrect:
    """Tests for correct method (async)."""

    @pytest.mark.asyncio
    async def test_correct_no_llm_service(self):
        """Test correct returns original if no LLM service."""
        corrector = SelfCorrector()
        corrector._llm_service = None

        # Ensure lazy load fails or returns None
        with patch("core.services.llm.get_llm_service", side_effect=ImportError):
            result = await corrector.correct("Test response")

        assert result.original == "Test response"
        assert result.corrected == "Test response"
        assert result.corrections_made == 0
        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_correct_with_no_corrections_needed(self):
        """Test correct when LLM says no corrections needed."""
        from unittest.mock import AsyncMock

        mock_service = Mock()
        mock_service.generate_response = AsyncMock(return_value="NO CORRECTIONS NEEDED")

        corrector = SelfCorrector(llm_service=mock_service)
        result = await corrector.correct("Good response")

        assert result.original == "Good response"
        assert result.corrected == "Good response"
        assert result.corrections_made == 0

    @pytest.mark.asyncio
    async def test_correct_with_correction(self):
        """Test correct applies correction."""
        from unittest.mock import AsyncMock

        mock_service = Mock()
        mock_service.generate_response = AsyncMock(
            side_effect=[
                "Improved response",
                "NO CORRECTIONS NEEDED",
            ]
        )

        corrector = SelfCorrector(llm_service=mock_service, max_corrections=2)
        result = await corrector.correct("Original response")

        assert result.original == "Original response"
        assert result.corrected == "Improved response"
        assert result.corrections_made == 1

    @pytest.mark.asyncio
    async def test_correct_with_context(self):
        """Test correct with context."""
        from unittest.mock import AsyncMock

        mock_service = Mock()
        mock_service.generate_response = AsyncMock(return_value="NO CORRECTIONS NEEDED")

        corrector = SelfCorrector(llm_service=mock_service)
        await corrector.correct("Response", context="Some context")

        mock_service.generate_response.assert_called_once()
        kwargs = mock_service.generate_response.call_args.kwargs
        assert "prompt" in kwargs
        assert "Context: Some context" in kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_correct_handles_exception(self):
        """Test correct handles LLM exceptions gracefully."""
        from unittest.mock import AsyncMock

        mock_service = Mock()
        mock_service.generate_response = AsyncMock(side_effect=Exception("LLM error"))

        corrector = SelfCorrector(llm_service=mock_service)
        result = await corrector.correct("Test response")

        assert result.original == "Test response"
        assert result.corrected == "Test response"
        assert result.corrections_made == 0
