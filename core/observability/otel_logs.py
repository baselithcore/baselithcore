"""OTLP log-record export: the third signal.

Traces and metrics already left this process over OTLP; logs did not. They
were written to stdout as JSON and picked up by whatever scraped the pod, which
works — until the question is "show me the log lines for *this* trace". The
structlog processor in :mod:`core.observability.logging` stamps ``trace_id`` and
``span_id`` on every entry, so the correlation data was being produced; it just
had to survive a file scrape, a parser and a second storage backend before a
backend could join on it. Exporting the records over OTLP hands the collector
the trace context as *structured* fields it already understands.

This does not replace stdout logging. Both run: the console/JSON handler stays
exactly as configured (it is what ``kubectl logs`` shows, and the only thing
left if the collector is down), and the OTLP handler is an additional sink.

Two loops this module is careful about:

* **Self-logging.** The exporter's own transport (gRPC, urllib3) and the OTel
  SDK log through the same stdlib root logger. Left unfiltered, one failed
  export logs a warning, which becomes a log record, which is queued for
  export, which fails. :class:`_SuppressExporterLoops` drops those records from
  the OTLP sink only — they still reach stdout, where a human can see them.
* **Re-entrancy on shutdown.** ``BatchLogRecordProcessor`` flushes on
  ``shutdown()``; the handler is detached from the root logger *first*, so
  nothing queues a record into a processor that is mid-flush.
"""

from __future__ import annotations

import logging
import threading
import warnings
from typing import Any, Final

from core.observability.logging import get_logger
from core.observability.otel_exporters import build_log_exporter

logger = get_logger(__name__)

#: Logger-name prefixes whose records are never exported over OTLP. Exporting
#: them is what turns one transport failure into an unbounded feedback loop.
_EXPORTER_LOGGER_PREFIXES: Final = (
    "opentelemetry",
    "urllib3",
    "grpc",
    "core.observability.otel",
)

_lock = threading.Lock()
_logger_provider: Any = None
_handler: logging.Handler | None = None


class _SuppressExporterLoops(logging.Filter):
    """Drop records emitted by the export path itself."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(_EXPORTER_LOGGER_PREFIXES)


def is_active() -> bool:
    """True once a LoggerProvider has been installed by this module."""
    return _logger_provider is not None


def _resolve_logging_handler() -> Any:
    """Return the stdlib-logging → OTel handler class.

    ``opentelemetry.sdk._logs.LoggingHandler`` emits a ``DeprecationWarning``
    from SDK 1.44 pointing at ``opentelemetry-instrumentation-logging``. That
    package is not a dependency here and its handler is not universally
    published yet, so the SDK class stays the implementation — preferred from
    the new location the moment it is importable, and with the warning silenced
    in the fallback path so an intentional choice does not surface as noise on
    every boot. Delete this helper once the new home is a hard dependency.
    """
    try:  # pragma: no cover - exercised only where the package is installed
        from opentelemetry.instrumentation.logging import (  # type: ignore[attr-defined]
            LoggingHandler as InstrumentationLoggingHandler,
        )

        return InstrumentationLoggingHandler
    except ImportError:
        pass

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from opentelemetry.sdk._logs import LoggingHandler as SDKLoggingHandler

    return SDKLoggingHandler


def setup_log_export(
    resource: Any,
    endpoint: str | None,
    protocol: str,
    *,
    console_export: bool = False,
    level: int = logging.NOTSET,
) -> Any | None:
    """Install a ``LoggerProvider`` and attach its handler to the root logger.

    Args:
        resource: The OTel ``Resource`` built by the telemetry bootstrap. Shared
            with traces and metrics so all three signals carry one identity.
        endpoint: OTLP collector endpoint, or ``None`` for no OTLP export.
        protocol: ``grpc`` or ``http/protobuf`` (see
            :mod:`core.observability.otel_exporters`).
        console_export: Also print log records through the OTel console
            exporter. For debugging the pipeline — the application's own
            stdout logging is unaffected and unrelated.
        level: Minimum level forwarded to the collector. ``NOTSET`` (default)
            forwards whatever the root logger already admits, so
            ``LOG_LEVEL`` stays the single knob.

    Returns:
        The installed provider, or ``None`` when there is nowhere to send
        records (no endpoint and no console) — a provider whose only processor
        exports nowhere is a batch thread doing pure waste.
    """
    global _logger_provider, _handler

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import (
        BatchLogRecordProcessor,
        ConsoleLogExporter,
        SimpleLogRecordProcessor,
    )

    LoggingHandler = _resolve_logging_handler()

    with _lock:
        if _logger_provider is not None:
            return _logger_provider

        if endpoint is None and not console_export:
            logger.info("[OTEL] Log export skipped (no OTLP endpoint, no console)")
            return None

        provider = LoggerProvider(resource=resource)

        if endpoint is not None:
            provider.add_log_record_processor(
                BatchLogRecordProcessor(build_log_exporter(endpoint, protocol))
            )
        if console_export:
            provider.add_log_record_processor(
                SimpleLogRecordProcessor(ConsoleLogExporter())
            )

        set_logger_provider(provider)

        handler = LoggingHandler(level=level, logger_provider=provider)
        handler.addFilter(_SuppressExporterLoops())
        logging.getLogger().addHandler(handler)

        _logger_provider = provider
        _handler = handler
        logger.info(
            "[OTEL] LoggerProvider installed (protocol=%s, export=%s)",
            protocol,
            endpoint if endpoint is not None else "console only",
        )
        return provider


def shutdown_log_export() -> None:
    """Detach the handler and flush the provider. Idempotent."""
    global _logger_provider, _handler

    with _lock:
        if _handler is not None:
            try:
                logging.getLogger().removeHandler(_handler)
            except Exception:  # silent-ok: detaching a handler during teardown
                # Nothing here may log: this runs inside shutdown, the handler
                # being removed is a log sink, and logging the failure to
                # remove a log sink is the re-entrancy this module exists to
                # avoid. The handler is dropped from the module state either
                # way, so a second shutdown cannot retry against a stale one.
                pass
            _handler = None

        if _logger_provider is not None:
            try:
                _logger_provider.shutdown()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("[OTEL] LoggerProvider shutdown error: %s", exc)
            _logger_provider = None


__all__ = ["is_active", "setup_log_export", "shutdown_log_export"]
