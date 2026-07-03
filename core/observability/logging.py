"""
Structured Logging Module.

This module provides a unified logging interface for BaselithCore.
It leverages `structlog` for high-performance, context-aware, and JSON-ready
logging, while maintaining a transparent fallback to the standard Python
`logging` library if specialized dependencies are missing.

Core Features:
1. Structured Outputs: Seamlessly switch between Rich-colored console and JSON.
2. Contextual Binding: Bind request IDs or user IDs to all log entries in a scope.
3. Foreign Log Hijacking: Standardizes logs from Uvicorn, FastAPI, and other libraries.
4. Thread/Async Safety: Uses ContextVars to maintain log context across boundaries.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar
from functools import lru_cache
from typing import Any

from core.config import get_app_config

# Check if structlog is available (soft dependency for lean environments).
try:
    import structlog
    from structlog.contextvars import bind_contextvars, unbind_contextvars

    STRUCTLOG_AVAILABLE = True
except ImportError:
    STRUCTLOG_AVAILABLE = False
    structlog = None  # type: ignore


# Internal context variable for request-scoped logging if structlog is absent.
# Default is None (not a shared mutable dict): a mutable ContextVar default is
# aliased across every context and can leak state between requests/tasks.
_log_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "log_context", default=None
)


def add_otel_context(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """
    structlog processor that injects the active OpenTelemetry trace context.

    Adds ``trace_id``/``span_id`` (zero-padded hex, W3C format) to every log
    entry when a span is recording, enabling trace↔log correlation in
    backends like Grafana/Tempo/Loki. No-op when the OTel SDK is absent or no
    span is active, so it is always safe to keep in the processor chain.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = f"{ctx.trace_id:032x}"
            event_dict["span_id"] = f"{ctx.span_id:016x}"
    except Exception:
        pass
    return event_dict


# --- Sensitive-data redaction (applied on every log entry) -----------------

_REDACTED = "[REDACTED]"

# Substrings that mark a structured field (or log kwarg) as a secret; the
# value is replaced wholesale. Mirrors the Sentry scrubber's key set.
_SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "session",
    "jwt",
    "bearer",
    "credential",
    "private_key",
    "access_key",
)
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-admin-token",
        "proxy-authorization",
    }
)

_EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# marker (:|=|whitespace) value  →  redact the value, keep the marker.
_CREDENTIALS_REGEX = re.compile(
    r"(?i)(api[-_]?key|authorization|bearer|token|secret|password|passwd)"
    r"(?P<sep>[:=\s]+)(?P<val>[^\s,;]+)"
)


def _key_is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _mask_email(match: re.Match[str]) -> str:
    email = match.group(0)
    try:
        user, domain = email.split("@")
    except ValueError:
        return "[EMAIL_REDACTED]"
    masked = (user[:3] + "***") if len(user) > 3 else (user[0] + "***")
    return f"{masked}@{domain}"


def _mask_text(value: str) -> str:
    value = _EMAIL_REGEX.sub(_mask_email, value)
    value = _CREDENTIALS_REGEX.sub(r"\1\g<sep>[REDACTED]", value)
    return value


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _mask_text(value)
    if isinstance(value, dict):
        return {
            k: (
                _REDACTED
                if isinstance(k, str) and _key_is_sensitive(k)
                else _redact_value(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def redact_url_credentials(url: str) -> str:
    """Return ``url`` with any embedded ``user:password@`` userinfo removed.

    Connection strings (``redis://:pass@host``, ``postgres://u:p@host``) must
    never be logged verbatim — the password would land in logs/Sentry. Logs the
    scheme/host/port/path only.
    """
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
    except Exception:
        return url
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: strip secrets from every log entry.

    Two complementary passes, so both structured kwargs and raw message strings
    are covered (the previous stdlib-filter approach saw neither structlog
    kwargs nor JSON-rendered fields):

    1. **By key** — any field whose name marks it a secret (``token``,
       ``authorization``, ``password``…) has its value replaced with
       ``[REDACTED]``, recursively into nested dicts/lists.
    2. **By value** — remaining string values (including the message ``event``)
       have emails masked and inline ``key=secret`` / ``Bearer <t>`` patterns
       redacted.

    Gated on ``log_masking_enabled`` (default on). Installed in both the
    structlog pipeline and the foreign-log pre-chain by :func:`configure_logging`,
    so it applies on the FastAPI/uvicorn path — not only the MCP server.
    """
    try:
        if not getattr(get_app_config(), "log_masking_enabled", True):
            return event_dict
    except Exception:
        pass

    for key in list(event_dict.keys()):
        if isinstance(key, str) and key != "event" and _key_is_sensitive(key):
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


def configure_logging(
    level: str | None = None,
    json_output: bool | None = None,
    add_timestamps: bool = True,
    stream: Any = sys.stdout,
) -> None:
    """
    Establish the global logging configuration.

    This should be called during the application's bootstrap phase (e.g., main.py).
    It configures processors for timestamps, log levels, and formatters
    for both stdout and foreign loggers.

    Args:
        level: Log level (DEBUG, INFO, etc.). Overrides system config if provided.
        json_output: If True, forces JSON format (useful for Splunk/ELK).
        add_timestamps: If True, injects ISO timestamps into every entry.
    """
    config = get_app_config()

    # Determine runtime settings from arguments or Pydantic config.
    log_level_str = level or config.log_level_console
    use_json = json_output if json_output is not None else config.log_json
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)

    if not STRUCTLOG_AVAILABLE:
        # Resilient fallback to basic logging to avoid system failure.
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=stream,
        )
        logging.getLogger().warning(
            "structlog not available, using standard logging. "
            "Install with: pip install structlog"
        )
        return

    # Configuration for timestamps and UTC alignment.
    fmt = "iso" if use_json else "%Y-%m-%d %H:%M:%S"
    utc = True if use_json else False

    # Define the shared pipeline for non-structlog (foreign) logs.
    common_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.contextvars.merge_contextvars,
        add_otel_context,
        structlog.processors.TimeStamper(fmt=fmt, utc=utc),
        # Redact secrets/PII from foreign (uvicorn/fastapi/3rd-party) logs.
        redact_sensitive,
    ]

    # Initialize the appropriate renderer based on the environment.
    final_renderer: Any
    if use_json:
        final_renderer = structlog.processors.JSONRenderer()
    else:
        try:
            import rich  # noqa: F401 # Attempt to use Rich for beautiful dev logs.

            exception_formatter = structlog.dev.rich_traceback
        except ImportError:
            exception_formatter = structlog.dev.plain_traceback
        final_renderer = structlog.dev.ConsoleRenderer(
            colors=True, exception_formatter=exception_formatter
        )

    # 1. Custom Formatter to bridge the gap between stdlib and structlog.
    class UnifiedFormatter(structlog.stdlib.ProcessorFormatter):
        """Standardizes inputs from 3rd party libraries into the structured pipeline."""

        def format(self, record: logging.LogRecord) -> str:
            if isinstance(record.msg, str):
                if record.msg.startswith("{") and record.msg.endswith("}"):
                    try:
                        import ast

                        record.msg = ast.literal_eval(record.msg)
                    except Exception:
                        pass
            return super().format(record)

    formatter = UnifiedFormatter(
        processor=final_renderer,
        foreign_pre_chain=common_processors,
    )

    # 2. Main structlog configuration.
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.contextvars.merge_contextvars,
            add_otel_context,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # Redact secrets/PII by key and mask credentials/emails in strings.
            # Runs after format_exc_info so rendered tracebacks are masked too,
            # and last before hand-off to the formatter.
            redact_sensitive,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 3. Clean environment and mount the unified handler to the root logger.
    root_l = logging.getLogger()
    for h in list(root_l.handlers):
        root_l.removeHandler(h)

    h = logging.StreamHandler(stream)
    h.setFormatter(formatter)
    root_l.addHandler(h)
    root_l.setLevel(log_level)

    # 4. Standardize Uvicorn and FastAPI logs to match the main system format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def get_log_config() -> dict[str, Any]:
    """
    Generate a Uvicorn-compatible logging configuration dictionary.

    Returns:
        Dict: Config suitable for `uvicorn.run(..., log_config=get_log_config())`.
    """
    config = get_app_config()
    use_json = config.log_json
    log_level = config.log_level_console.upper()

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "structlog.stdlib.ProcessorFormatter",
                "processor": "structlog.dev.ConsoleRenderer"
                if not use_json
                else "structlog.processors.JSONRenderer",
                "foreign_pre_chain": [
                    "structlog.stdlib.add_log_level",
                    "structlog.stdlib.add_logger_name",
                    "structlog.stdlib.PositionalArgumentsFormatter",
                    "structlog.processors.TimeStamper",
                ],
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["default"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["default"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["default"],
                "level": "WARNING",
                "propagate": False,
            },
        },
    }


class SafeLogger:
    """
    Fallback wrapper for standard logging when structlog is missing.

    Ensures that modern kwarg-based logging (e.g. log.info("msg", key="val"))
    remains functional and readable in standard text logs.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _format(self, msg: Any, **kwargs: Any) -> str:
        if not kwargs:
            return str(msg)
        pairs = " ".join(f"{k}={v}" for k, v in kwargs.items())
        return f"{msg} [{pairs}]"

    def info(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._logger.info(self._format(msg, **kwargs), *args)

    def error(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._logger.error(self._format(msg, **kwargs), *args)

    def warning(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._logger.warning(self._format(msg, **kwargs), *args)

    def debug(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._logger.debug(self._format(msg, **kwargs), *args)

    def critical(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._logger.critical(self._format(msg, **kwargs), *args)

    def exception(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._logger.exception(self._format(msg, **kwargs), *args)


@lru_cache(maxsize=128)
def get_logger(name: str | None = None) -> Any:
    """
    Retrieve a logger instance for a given module.

    Args:
        name: Typically `__name__`. Defaults to 'app' if not provided.

    Returns:
        A structlog BoundLogger or a SafeLogger wrapper depending on availability.
    """
    if STRUCTLOG_AVAILABLE:
        return structlog.get_logger(name or "app")
    return SafeLogger(logging.getLogger(name or "app"))


class bind_context:
    """
    Context manager for request-scoped logging metadata.

    Usage:
        with bind_context(request_id="abc-123"):
            log.info("Processing")  # Logs as: {"msg": "Processing", "request_id": "abc-123"}
    """

    def __init__(self, **kwargs: Any):
        self.context = kwargs
        self._previous: dict[str, Any] = {}

    def __enter__(self) -> bind_context:
        if STRUCTLOG_AVAILABLE:
            bind_contextvars(**self.context)
        else:
            # ContextVar fallback for thread/async safety in standard logging.
            current = _log_context.get() or {}
            self._previous = current.copy()
            _log_context.set({**current, **self.context})
        return self

    def __exit__(self, *args: Any) -> None:
        if STRUCTLOG_AVAILABLE:
            unbind_contextvars(*self.context.keys())
        else:
            _log_context.set(self._previous)


def add_context(**kwargs: Any) -> None:
    """
    Bind metadata to all subsequent logs in the current execution context.
    """
    if STRUCTLOG_AVAILABLE:
        bind_contextvars(**kwargs)
    else:
        current = _log_context.get() or {}
        _log_context.set({**current, **kwargs})


def clear_context() -> None:
    """
    Flush all active metadata from the logging context.
    """
    if STRUCTLOG_AVAILABLE:
        structlog.contextvars.clear_contextvars()
    else:
        _log_context.set({})


# Lifecycle management.
_configured = False


def ensure_configured(stream: Any = sys.stdout) -> None:
    """
    Safe entry point to guarantee logging setup without double initialization.
    """
    global _configured
    if not _configured:
        configure_logging(stream=stream)
        _configured = True


__all__ = [
    "STRUCTLOG_AVAILABLE",
    "add_context",
    "add_otel_context",
    "bind_context",
    "clear_context",
    "configure_logging",
    "ensure_configured",
    "get_logger",
    "redact_sensitive",
    "redact_url_credentials",
]
