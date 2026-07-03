from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_llm_service():
    """
    Mock LLMService for all unit tests to prevent real API calls and configuration errors.
    This fixture automatically patches `core.services.llm.get_llm_service`.
    """
    with patch("core.services.llm.get_llm_service") as mock_get:
        mock_service = MagicMock()
        # generate_response is async on the real LLMService — mock it as such
        # so callers that `await` it get a value, not an un-awaitable Mock.
        mock_service.generate_response = AsyncMock(return_value="Mocked LLM Response")
        # Mock streaming response
        mock_service.generate_response_stream.return_value = iter(
            ["Mock", "ed", " stream"]
        )

        mock_get.return_value = mock_service
        yield mock_get


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    """
    Reset all global circuit breakers before each test to prevent state
    persistence (e.g., trips from previous 'raises_on_error' tests).
    """
    from core.resilience.circuit_breaker import (
        CircuitState,
        CircuitStats,
        _circuit_breakers,
    )

    for cb in _circuit_breakers.values():
        cb._state = CircuitState.CLOSED
        cb._stats = CircuitStats()
        cb._half_open_attempts = 0
