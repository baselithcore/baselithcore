import pytest
from unittest.mock import MagicMock, patch

from core.evaluation.service import EvaluationService
from core.events import EventNames


@pytest.fixture
def mock_event_bus():
    return MagicMock()


def test_evaluation_service_startup_enabled(mock_event_bus):
    """Test that service starts and subscribes when enabled via config."""
    with patch("core.config.evaluation.evaluation_config") as mock_config:
        mock_config.enabled = True

        service = EvaluationService(event_bus=mock_event_bus)
        service.start()

        # Should subscribe to FLOW_COMPLETED
        mock_event_bus.subscribe.assert_called_with(
            EventNames.FLOW_COMPLETED, service._on_flow_completed
        )
        assert service._running is True


def test_evaluation_service_startup_disabled(mock_event_bus):
    """Test that service does not start when disabled via config."""
    with patch("core.config.evaluation.evaluation_config") as mock_config:
        mock_config.enabled = False

        service = EvaluationService(event_bus=mock_event_bus)
        service.start()

        # Should NOT subscribe
        mock_event_bus.subscribe.assert_not_called()
        assert service._running is False
