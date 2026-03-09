import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def mock_llm_service():
    """
    Mock LLMService for all unit tests to prevent real API calls and configuration errors.
    This fixture automatically patches `core.services.llm.get_llm_service`.
    """
    with patch("core.services.llm.get_llm_service") as mock_get:
        mock_service = MagicMock()
        # Mock standard response
        mock_service.generate_response.return_value = "Mocked LLM Response"
        # Mock streaming response
        mock_service.generate_response_stream.return_value = iter(
            ["Mock", "ed", " stream"]
        )

        mock_get.return_value = mock_service
        yield mock_get
