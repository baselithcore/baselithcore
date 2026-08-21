from unittest.mock import MagicMock, patch

from core.observability.logging import (
    SafeLogger,
    bind_context,
    configure_logging,
    get_logger,
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
    # configure_logging also mutates the REAL stdlib root logger; with
    # structlog mocked its handlers/formatters become MagicMocks, and leaving
    # them installed poisons every later log write in the process (any test
    # that logs then dies with "write() argument must be str"). Snapshot and
    # restore the root logger so the pollution cannot escape this test.
    import logging as _logging

    root = _logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        with patch("core.observability.logging.STRUCTLOG_AVAILABLE", True):
            configure_logging(level="DEBUG")
            assert mock_structlog.configure.called
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_bind_context():
    # Test context binding works (even if just mocked fallback)
    with bind_context(request_id="123"):
        # Just ensure it doesn't crash on fallback logic
        pass


def test_safe_logger_escapes_newlines_in_kwargs():
    """A kwarg carrying a newline must not forge a second log entry."""
    mock_logger = MagicMock()
    SafeLogger(mock_logger).info("Login", user="bob\nERROR:root:forged")

    rendered = mock_logger.info.call_args[0][0]
    assert "\n" not in rendered
    assert "bob\\x0aERROR:root:forged" in rendered


def test_safe_logger_redacts_sensitive_kwargs():
    """The stdlib fallback path must redact secrets like the structlog one."""
    mock_logger = MagicMock()
    SafeLogger(mock_logger).info("Auth", api_key="super-secret", user="bob")

    rendered = mock_logger.info.call_args[0][0]
    assert "super-secret" not in rendered
    assert "api_key=[REDACTED]" in rendered
    assert "user=bob" in rendered
