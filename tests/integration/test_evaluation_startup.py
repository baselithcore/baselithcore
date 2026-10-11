from unittest.mock import MagicMock

import pytest

from baselith.core.config.evaluation import EvaluationConfig
from baselith.core.evaluation.service import EvaluationService
from baselith.core.events import EventNames


@pytest.fixture
def mock_event_bus():
    return MagicMock()


def test_evaluation_service_startup_enabled(mock_event_bus):
    """Test that service starts and subscribes when enabled via config."""
    service = EvaluationService(
        event_bus=mock_event_bus, config=EvaluationConfig(enabled=True)
    )
    service.start()

    # Should subscribe to FLOW_COMPLETED
    mock_event_bus.subscribe.assert_called_with(
        EventNames.FLOW_COMPLETED, service._on_flow_completed
    )
    assert service._running is True


def test_evaluation_service_startup_disabled(mock_event_bus):
    """Test that service does not start when disabled via config."""
    service = EvaluationService(
        event_bus=mock_event_bus, config=EvaluationConfig(enabled=False)
    )
    service.start()

    # Should NOT subscribe
    mock_event_bus.subscribe.assert_not_called()
    assert service._running is False
