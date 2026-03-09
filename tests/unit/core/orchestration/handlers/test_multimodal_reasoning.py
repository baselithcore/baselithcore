"""
Tests for MultiModalReasoningHandler.

Tests the combination of Vision analysis with Tree of Thoughts reasoning.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.orchestration.handlers.multimodal_reasoning import MultiModalReasoningHandler
from core.services.vision import VisionResponse, ImageContent


@pytest.fixture
def mock_vision_service():
    """Create a mock VisionService."""
    service = MagicMock()
    service.analyze = AsyncMock(
        return_value=VisionResponse(
            success=True,
            content="L'immagine mostra un diagramma di architettura con 3 componenti: Frontend, Backend e Database.",
            provider="openai",
            model="gpt-4o",
            tokens_used=100,
        )
    )
    return service


@pytest.fixture
def mock_llm_service():
    """Create a mock LLMService."""
    service = MagicMock()
    service.generate_response = MagicMock(
        return_value="Analizzando il diagramma, il flusso dei dati procede dal Frontend al Backend."
    )
    return service


@pytest.fixture
def mock_tot_engine():
    """Create a mock TreeOfThoughtsAsync engine."""
    engine = MagicMock()
    engine.solve = AsyncMock(
        return_value={
            "solution": "Il diagramma mostra un'architettura a 3 livelli. "
            "1. Il Frontend comunica con il Backend via API REST. "
            "2. Il Backend processa le richieste e interagisce con il Database. "
            "3. I dati fluiscono in modo bidirezionale tra Backend e Database.",
            "steps": [
                "Identificato Frontend",
                "Identificato Backend",
                "Identificato Database",
                "Analizzate connessioni",
            ],
            "tree_data": None,
        }
    )
    return engine


@pytest.fixture
def handler(mock_vision_service, mock_llm_service, mock_tot_engine):
    """Create handler with mocked dependencies."""
    h = MultiModalReasoningHandler(
        vision_service=mock_vision_service,
        llm_service=mock_llm_service,
    )
    h._tot_engine = mock_tot_engine
    return h


class TestMultiModalReasoningHandler:
    """Tests for MultiModalReasoningHandler."""

    @pytest.mark.asyncio
    async def test_handle_with_image_paths(
        self, handler, mock_vision_service, mock_tot_engine
    ):
        """Test handling request with image file paths."""
        context = {
            "image_paths": ["/path/to/diagram.png"],
        }
        query = "Analizza questo diagramma e spiega il flusso dei dati"

        # Mock ImageContent.from_file to avoid actual file access
        with patch.object(ImageContent, "from_file", return_value=MagicMock()):
            result = await handler.handle(query, context)

        assert "response" in result
        assert "vision_analysis" in result
        assert "reasoning_steps" in result
        assert result["metadata"]["images_analyzed"] == 1
        assert not result.get("error")

        # Vision service should be called
        mock_vision_service.analyze.assert_called_once()

        # ToT engine should be called
        mock_tot_engine.solve.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_without_images(self, handler):
        """Test handling request without any images returns error."""
        context = {}
        query = "Analizza questo diagramma"

        result = await handler.handle(query, context)

        assert result.get("error") is True
        assert "image" in result["response"].lower()

    @pytest.mark.asyncio
    async def test_handle_with_base64_images(self, handler, mock_vision_service):
        """Test handling request with base64 image data."""
        context = {
            "image_data": ["base64encodedimagedata=="],
        }
        query = "Cosa vedi nell'immagine?"

        with patch.object(ImageContent, "from_base64", return_value=MagicMock()):
            result = await handler.handle(query, context)

        assert "response" in result
        assert result["metadata"]["images_analyzed"] == 1

    @pytest.mark.asyncio
    async def test_enriched_problem_includes_vision_context(
        self, handler, mock_vision_service, mock_tot_engine
    ):
        """Test that ToT receives enriched problem with vision context."""
        context = {"image_paths": ["/path/to/image.png"]}
        query = "Spiega il contenuto"

        with patch.object(ImageContent, "from_file", return_value=MagicMock()):
            await handler.handle(query, context)

        # Get the problem passed to ToT
        call_args = mock_tot_engine.solve.call_args
        enriched_problem = call_args.kwargs.get(
            "problem", call_args.args[0] if call_args.args else ""
        )

        # Should contain vision analysis context
        assert "Visual Context" in enriched_problem
        assert (
            "diagram" in enriched_problem.lower()
            or "architecture" in enriched_problem.lower()
        )

    @pytest.mark.asyncio
    async def test_reasoning_params_from_context(self, handler, mock_tot_engine):
        """Test that reasoning parameters are extracted from context."""
        context = {
            "image_paths": ["/path/to/image.png"],
            "branching_factor": 5,
            "max_reasoning_steps": 6,
            "strategy": "mcts",
        }
        query = "Analizza"

        with patch.object(ImageContent, "from_file", return_value=MagicMock()):
            await handler.handle(query, context)

        call_kwargs = mock_tot_engine.solve.call_args.kwargs
        assert call_kwargs.get("k") == 5
        assert call_kwargs.get("max_steps") == 6
        assert call_kwargs.get("strategy") == "mcts"

    @pytest.mark.asyncio
    async def test_handle_vision_service_failure(self, handler, mock_vision_service):
        """Test graceful handling when vision service fails."""
        mock_vision_service.analyze.side_effect = Exception("Vision API error")
        context = {"image_paths": ["/path/to/image.png"]}

        with patch.object(ImageContent, "from_file", return_value=MagicMock()):
            result = await handler.handle("Analizza", context)

        assert result.get("error") is True
        assert result["vision_analysis"] is None

    @pytest.mark.asyncio
    async def test_metadata_includes_all_fields(self, handler, mock_vision_service):
        """Test that metadata contains expected fields."""
        context = {"image_paths": ["/path/to/image.png"]}

        with patch.object(ImageContent, "from_file", return_value=MagicMock()):
            result = await handler.handle("Analizza diagramma", context)

        metadata = result.get("metadata", {})
        assert "images_analyzed" in metadata
        assert "vision_provider" in metadata
        assert "reasoning_strategy" in metadata
        assert "reasoning_depth" in metadata


class TestMultiModalReasoningHandlerIntegration:
    """Integration-style tests for the handler."""

    def test_handler_exports(self):
        """Test that handler is properly exported."""
        from core.orchestration.handlers import MultiModalReasoningHandler

        assert MultiModalReasoningHandler is not None

    def test_orchestrator_registers_handler(self):
        """Test that Orchestrator registers the multimodal reasoning handler."""
        from core.orchestration import Orchestrator

        orchestrator = Orchestrator()
        intents = orchestrator.get_registered_intents()

        assert "multimodal_reasoning" in intents

    def test_intent_classification_patterns(self):
        """Test that intent patterns are registered correctly."""
        from core.orchestration import Orchestrator

        orchestrator = Orchestrator()
        available = orchestrator.intent_classifier.get_available_intents()

        assert "multimodal_reasoning" in available
