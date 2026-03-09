import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from core.learning.evolution import EvolutionService
from core.events import EventBus, EventNames


@pytest.fixture
def mock_memory_manager():
    memory = MagicMock()
    memory.remember = AsyncMock()
    return memory


@pytest.fixture
def mock_event_bus():
    return EventBus()


@pytest.mark.asyncio
async def test_evolution_service_low_score(mock_memory_manager, mock_event_bus):
    """Test that low score triggers lesson learned storage."""

    # Setup
    with patch("core.learning.evolution.get_event_bus", return_value=mock_event_bus):
        service = EvolutionService(memory_manager=mock_memory_manager)
        service.start()

        # Simulate Evaluation Event (Low Score)
        event_data = {
            "intent": "test_failure",
            "score": 0.2,
            "quality": "poor",
            "feedback": "Response was irrelevant.",
        }

        # Emit event
        await mock_event_bus.emit(EventNames.EVALUATION_COMPLETED, event_data)

        # Wait a bit for async task processing (EventBus emit is async but listeners run in background tasks usually?
        # Actually EventBus in this repo seems to run sync or async depending on implementation.
        # Let's assume we need to wait a tick.)
        await asyncio.sleep(0.1)

        # Verify Memory Storage
        mock_memory_manager.remember.assert_called_once()
        call_args = mock_memory_manager.remember.call_args
        assert "Lesson Learned" in call_args.kwargs["metadata"]["title"]
        assert "test_failure" in call_args.kwargs["content"]

        service.stop()


@pytest.mark.asyncio
async def test_evolution_service_high_score(mock_memory_manager, mock_event_bus):
    """Test that high score triggers best practice storage."""

    with patch("core.learning.evolution.get_event_bus", return_value=mock_event_bus):
        service = EvolutionService(memory_manager=mock_memory_manager)
        service.start()

        event_data = {
            "intent": "test_success",
            "score": 0.95,
            "quality": "excellent",
            "feedback": "Perfect response.",
        }

        await mock_event_bus.emit(EventNames.EVALUATION_COMPLETED, event_data)
        await asyncio.sleep(0.1)

        mock_memory_manager.remember.assert_called_once()
        call_args = mock_memory_manager.remember.call_args
        assert "Best Practice" in call_args.kwargs["metadata"]["title"]

        service.stop()
