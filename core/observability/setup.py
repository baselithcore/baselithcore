"""
Logging setup and filters.

Configures root logging with request ID tracking and sensitive data masking.
"""

import contextvars
import logging
import os
import sys
from typing import Any

from core.config import get_app_config
from core.observability.redaction import _mask_text

_app_config = get_app_config()

LOG_LEVEL_CONSOLE = getattr(_app_config, "log_level_console", "INFO")
LOG_LEVEL_FILE = getattr(_app_config, "log_level_file", "INFO")

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(request_id)s | %(message)s"
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

_old_record_factory = logging.getLogRecordFactory()


def _record_factory(
    *args: Any, **kwargs: Any
) -> logging.LogRecord:  # pragma: no cover - logging infra
    record = _old_record_factory(*args, **kwargs)
    if not hasattr(record, "request_id"):
        try:
            record.request_id = request_id_ctx.get("-")
        except Exception:  # silent-ok: LogRecord factory; logging here recurses
            record.request_id = "-"
    return record


logging.setLogRecordFactory(_record_factory)


class RequestIdFilter(logging.Filter):
    """Filter to inject request_id into every log record."""

    def filter(
        self, record: logging.LogRecord
    ) -> bool:  # pragma: no cover - logging filter
        record.request_id = request_id_ctx.get("-")
        return True


class SensitiveDataFilter(logging.Filter):
    """Redacts sensitive values (credentials, emails) from stdlib log records.

    Applies the same value masking as the structlog processor
    (:func:`core.observability.redaction.redact_sensitive`), so a handler
    behind this filter cannot leak what the structlog pipeline masks.
    """

    def filter(
        self, record: logging.LogRecord
    ) -> bool:  # pragma: no cover - logging filter
        if not getattr(_app_config, "log_masking_enabled", False):
            return True

        try:
            msg = str(record.getMessage())
        # A bad format arg is the handler's to report; this filter passes it on.
        except Exception:  # silent-ok: logging.Filter; logging here recurses
            return True

        record.msg = _mask_text(msg)
        record.args = ()
        return True


def ensure_logging_configured(stream: Any = sys.stdout) -> None:
    """
    Ensures that logging is properly configured.
    Delegates to core.observability.logging.configure_logging for structlog initial setup
    and then adds specific filters and file handlers defined in this module.
    """
    from core.observability.logging import configure_logging

    # 0. Initialize structlog properly to generate the beautiful messages
    # This also sets up the root logger with a StreamHandler using ProcessorFormatter
    use_json = getattr(_app_config, "log_json", False)
    configure_logging(level=LOG_LEVEL_CONSOLE, json_output=use_json, stream=stream)

    # 1. Configures the root logger further
    root_logger = logging.getLogger()

    # Create logs directory if it doesn't exist. Default is the CWD-relative
    # "logs/", but deployments can pin an ABSOLUTE path via BASELITH_LOG_DIR so
    # the file sink does not depend on the process working directory and can be
    # placed on a writable, log-shipper-mounted volume (e.g. a Promtail scrape
    # path, or a systemd ReadWritePaths dir under ProtectSystem=strict).
    # A blank value (the shipped template leaves ``BASELITH_LOG_DIR=``) means
    # the default, not "the empty path": os.makedirs("") raises and the sink
    # would silently land in the CWD as a bare ``app.log``.
    log_dir = os.environ.get("BASELITH_LOG_DIR", "").strip() or "logs"
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception as exc:
        # Surface to stderr: structlog file logging silently absent otherwise.
        print(
            f"[observability] WARNING: could not create log dir '{log_dir}': {exc}; "
            "file logging disabled.",
            file=sys.stderr,
        )

    # The root logger must accept the minimum level between console and file to allow both to function
    level_console = getattr(logging, LOG_LEVEL_CONSOLE, logging.INFO)
    level_file = getattr(logging, LOG_LEVEL_FILE, logging.INFO)
    min_level = min(level_console, level_file)
    root_logger.setLevel(min_level)

    # Apply filters to existing handlers (structlog already created them in logging.py)
    # We do not want to remove structlog's handler and recreate our own, otherwise we lose formatting
    for handler in root_logger.handlers:
        handler.addFilter(RequestIdFilter())
        handler.addFilter(SensitiveDataFilter())

    # Check if a file handler already exists
    has_file_handler = any(
        isinstance(h, logging.FileHandler) for h in root_logger.handlers
    )

    # 2. Add File Handler if needed
    if not has_file_handler:
        try:
            # We must use the message formatting that accepts structlog's full output
            formatter = logging.Formatter("%(message)s")

            file_handler = logging.FileHandler(os.path.join(log_dir, "app.log"))
            file_handler.setLevel(level_file)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(RequestIdFilter())
            file_handler.addFilter(SensitiveDataFilter())
            root_logger.addHandler(file_handler)
        except Exception as exc:
            print(
                f"[observability] WARNING: file logging disabled "
                f"('{os.path.join(log_dir, 'app.log')}'): {exc}",
                file=sys.stderr,
            )
