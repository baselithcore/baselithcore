import pytest
import asyncio
from unittest.mock import MagicMock, patch
from core.observability.audit import (
    AuditLogger,
    AuditEventType,
    FileAuditSink,
    LoggerAuditSink,
    AuditEvent,
)


@pytest.mark.asyncio
async def test_audit_logger_logs_async():
    mock_sink = MagicMock()

    # mock_sink.write is async in protocol
    async def async_write(event):
        mock_sink.write(event)

    mock_sink.write = MagicMock()
    mock_sink.write.side_effect = None  # ensure it's not raising

    # We need an object that has an awaitable write method
    class MockAsyncSink:
        async def write(self, event):
            mock_sink.write(event)

    sink = MockAsyncSink()
    logger = AuditLogger(sinks=[sink])

    await logger.log(AuditEventType.AUTH_LOGIN, user_id="u1")

    mock_sink.write.assert_called_once()
    event = mock_sink.write.call_args[0][0]
    assert isinstance(event, AuditEvent)
    assert event.event_type == AuditEventType.AUTH_LOGIN
    assert event.user_id == "u1"


@pytest.mark.asyncio
async def test_file_audit_sink_runs_in_executor(tmp_path):
    # Use tmp_path to allow directory creation in init
    log_path = tmp_path / "audit.log"

    with patch("core.observability.audit.asyncio.get_running_loop") as mock_get_loop:
        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop
        # Make run_in_executor return a Future-like object (awaitable)
        f = asyncio.Future()
        f.set_result(None)
        mock_loop.run_in_executor.return_value = f

        sink = FileAuditSink(path=log_path)
        event = AuditEvent(AuditEventType.CUSTOM, action="test")

        await sink.write(event)

        mock_loop.run_in_executor.assert_called_once()
        # Check args: (None, sink._append_to_file, payload)
        args = mock_loop.run_in_executor.call_args[0]
        assert args[0] is None
        assert args[1] == sink._append_to_file
        # payload is properly formatted
        assert isinstance(args[2], str)


@pytest.mark.asyncio
async def test_logger_audit_sink():
    mock_logger = MagicMock()
    sink = LoggerAuditSink(logger=mock_logger)

    event = AuditEvent(AuditEventType.CUSTOM, action="test")
    await sink.write(event)

    mock_logger.info.assert_called()
