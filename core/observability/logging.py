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
import sys
from contextvars import ContextVar
from functools import lru_cache
from typing import Any, Dict, Optional

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
_log_context: ContextVar[Dict[str, Any]] = ContextVar("log_context", default={})


def configure_logging(
    level: Optional[str] = None,
    json_output: Optional[bool] = None,
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
        structlog.processors.TimeStamper(fmt=fmt, utc=utc),
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
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
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


def get_log_config() -> Dict[str, Any]:
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
def get_logger(name: Optional[str] = None) -> Any:
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
        self._previous: Dict[str, Any] = {}

    def __enter__(self) -> "bind_context":
        if STRUCTLOG_AVAILABLE:
            bind_contextvars(**self.context)
        else:
            # ContextVar fallback for thread/async safety in standard logging.
            current = _log_context.get()
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
        current = _log_context.get()
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
    "configure_logging",
    "get_logger",
    "bind_context",
    "add_context",
    "clear_context",
    "ensure_configured",
    "STRUCTLOG_AVAILABLE",
]
