"""
Unit tests for optimizer module.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.optimization.optimizer import PromptOptimizer


@pytest.fixture
def mock_feedback_collector():
    collector = AsyncMock()
    return collector


@pytest.fixture
def mock_llm_service():
    with patch("core.services.llm.get_llm_service") as mock_get:
        service = AsyncMock()
        mock_get.return_value = service
        yield service


@pytest.mark.asyncio
async def test_analyze_performance(mock_feedback_collector):
    optimizer = PromptOptimizer(mock_feedback_collector)

    # Mock feedback data
    mock_feedback_collector.get_all_feedback.return_value = []

    suggestions = await optimizer.analyze_performance()
    assert isinstance(suggestions, list)
    assert len(suggestions) == 0

    # Add some mock feedback logic if needed, but basic flow is covered


@pytest.mark.asyncio
async def test_auto_tune(mock_feedback_collector, mock_llm_service):
    optimizer = PromptOptimizer(mock_feedback_collector)
    # Ensure llm_service is loaded
    _ = optimizer.llm_service

    # Mock data for auto_tune
    agent_id = "agent1"
    mock_feedback_collector.get_agent_performance.return_value = {"average_score": 0.4}

    mock_feedback_item = MagicMock()
    mock_feedback_item.agent_id = agent_id
    mock_feedback_item.score = 0.2
    mock_feedback_item.comment = "Bad response"

    mock_feedback_collector.get_all_feedback.return_value = [mock_feedback_item]

    # Mock LLM response
    mock_llm_service.generate_response.return_value = "Improved Prompt"

    result = await optimizer.auto_tune(agent_id)
    assert result is not None
    assert result.agent_id == agent_id
    assert result.suggestion == "Improved Prompt"
    assert result.applied is False
    assert result.previous_score == 0.4
    mock_llm_service.generate_response.assert_called_once()
