from unittest.mock import MagicMock, patch

from baselith.core.observability.logging import (
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


@patch("baselith.core.observability.logging.structlog")
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
        with patch("baselith.core.observability.logging.STRUCTLOG_AVAILABLE", True):
            configure_logging(level="DEBUG")
            assert mock_structlog.configure.called
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_configure_logging_uses_queue_handler_and_delivers():
    """The root logger's write must be non-blocking: records go through a
    QueueHandler and a background QueueListener owns the stream write. A logged
    record still reaches the stream once the listener drains."""
    import io
    import logging as _logging
    from logging.handlers import QueueHandler

    from baselith.core.observability import logging as log_mod

    root = _logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        buf = io.StringIO()
        log_mod.configure_logging(level="INFO", stream=buf)

        # Root logger hands off via a QueueHandler, not a direct StreamHandler.
        assert any(isinstance(h, QueueHandler) for h in root.handlers)
        # A listener thread owns the blocking write.
        assert log_mod._log_listener is not None

        _logging.getLogger("test.queue").info("hello-queue-handler")
        # stop() drains the queue before terminating the thread → record flushed.
        log_mod._stop_log_listener()
        assert "hello-queue-handler" in buf.getvalue()
    finally:
        log_mod._stop_log_listener()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_stop_log_listener_is_idempotent():
    """Stopping when no listener is active must be a harmless no-op."""
    from baselith.core.observability import logging as log_mod

    log_mod._stop_log_listener()
    log_mod._stop_log_listener()  # second call must not raise
    assert log_mod._log_listener is None


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


def _json_lines_logged(emit) -> list[dict]:
    """Configure JSON logging into a buffer, run ``emit``, return the entries."""
    import io
    import json
    import logging as _logging

    from baselith.core.observability import logging as log_mod

    root = _logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        buf = io.StringIO()
        log_mod.configure_logging(level="INFO", json_output=True, stream=buf)
        emit(log_mod)
        log_mod._stop_log_listener()
        return [json.loads(line) for line in buf.getvalue().splitlines() if line]
    finally:
        log_mod._stop_log_listener()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_a_value_without_a_literal_repr_does_not_drop_the_line():
    """The queue hand-off used to ``repr`` the event dict and parse it back:
    a UUID, a Path or an exception in a kwarg failed the parse and the entry
    was lost to a "--- Logging error ---" on stderr."""
    import uuid
    from pathlib import Path

    run = uuid.uuid4()

    def emit(log_mod) -> None:
        log = log_mod.get_logger("test.event_dict")
        log.info("with-uuid", run=run)
        log.info("with-path", path=Path("/srv/data"))
        log.info("with-error", cause=ValueError("boom"))

    entries = {e["event"]: e for e in _json_lines_logged(emit)}

    assert str(run) in entries["with-uuid"]["run"]
    assert "/srv/data" in entries["with-path"]["path"]
    assert "boom" in entries["with-error"]["cause"]


def test_structlog_and_foreign_entries_both_carry_a_timestamp():
    import logging as _logging

    def emit(log_mod) -> None:
        log_mod.get_logger("test.native").info("native-entry", n=1)
        _logging.getLogger("test.foreign").info("foreign-entry %s", 2)

    entries = {e["event"]: e for e in _json_lines_logged(emit)}

    assert entries["native-entry"]["n"] == 1
    assert entries["native-entry"]["timestamp"]
    assert entries["foreign-entry 2"]["timestamp"]


def test_a_foreign_message_that_looks_like_a_dict_stays_text():
    import logging as _logging

    def emit(log_mod) -> None:
        _logging.getLogger("test.foreign").info("{'looks': 'like a dict'}")

    (entry,) = _json_lines_logged(emit)

    assert entry["event"] == "{'looks': 'like a dict'}"
