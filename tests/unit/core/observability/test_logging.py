from unittest.mock import patch, MagicMock
from core.observability.logging import (
    configure_logging,
    get_logger,
    bind_context,
    SafeLogger,
)


def test_get_logger_returns_logger():
    logger = get_logger("test_logger")
    assert logger is not None


def test_safe_logger_formatting():
    mock_logger = MagicMock()
    safe_logger = SafeLogger(mock_logger)

    safe_logger.info("Test message", key="value")

    mock_logger.info.assert_called_with("Test message [key=value]")


@patch("core.observability.logging.structlog")
def test_configure_logging_uses_structlog_if_available(mock_structlog):
    # Mock structlog being available
    with patch("core.observability.logging.STRUCTLOG_AVAILABLE", True):
        configure_logging(level="DEBUG")
        assert mock_structlog.configure.called


def test_bind_context():
    # Test context binding works (even if just mocked fallback)
    with bind_context(request_id="123"):
        # Just ensure it doesn't crash on fallback logic
        pass
