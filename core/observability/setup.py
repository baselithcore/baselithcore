"""
Logging setup and filters.

Configures root logging with request ID tracking and sensitive data masking.
"""

import contextvars
import logging
import os
import re

from core.config import get_app_config

_app_config = get_app_config()

LOG_LEVEL_CONSOLE = getattr(_app_config, "log_level_console", "INFO")
LOG_LEVEL_FILE = getattr(_app_config, "log_level_file", "INFO")

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(request_id)s | %(message)s"
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

_old_record_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs):  # pragma: no cover - logging infra
    record = _old_record_factory(*args, **kwargs)
    if not hasattr(record, "request_id"):
        try:
            record.request_id = request_id_ctx.get("-")
        except Exception:
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
    """
    Redacts sensitive values (tokens, api keys, PII) from logs.
    Uses regex for case-insensitive replacement and to find specific patterns (e.g., email).
    """

    # Email regex (simplified)
    EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")

    # Common key/token pattern regex (e.g., "api_key=xyz", "Bearer xyz")
    # Captures: (marker)(=|:|\s+)(value)
    # Example: "Authorization: Bearer xyz" -> "Authorization: [REDACTED]"
    CREDENTIALS_REGEX = re.compile(
        r"(?i)(api[-_]?key|authorization|bearer|token|secret|password|passwd)(?P<sep>[:=\s]+)(?P<val>[^\s]+)",
    )

    def filter(
        self, record: logging.LogRecord
    ) -> bool:  # pragma: no cover - logging filter
        if not getattr(_app_config, "log_masking_enabled", False):
            return True

        try:
            msg = str(record.getMessage())
        except Exception:
            return True

        # 1. Mask PII (Emails)
        # Replaces emails with e***@domain.com
        def mask_email(match):
            email = match.group(0)
            try:
                user, domain = email.split("@")
                if len(user) > 3:
                    masked_user = user[:3] + "***"
                else:
                    masked_user = user[0] + "***"
                return f"{masked_user}@{domain}"
            except ValueError:
                return "[EMAIL_REDACTED]"

        msg = self.EMAIL_REGEX.sub(mask_email, msg)

        # 2. Mask Credentials
        msg = self.CREDENTIALS_REGEX.sub(r"\1\g<sep>[REDACTED]", msg)

        record.msg = msg
        record.args = ()
        return True


def ensure_logging_configured() -> None:
    """
    Ensures that logging is properly configured.
    Delegates to core.observability.logging.configure_logging for structlog initial setup
    and then adds specific filters and file handlers defined in this module.
    """
    from core.observability.logging import configure_logging

    # 0. Initialize structlog properly to generate the beautiful messages
    # This also sets up the root logger with a StreamHandler using ProcessorFormatter
    use_json = getattr(_app_config, "log_json", False)
    configure_logging(level=LOG_LEVEL_CONSOLE, json_output=use_json)

    # 1. Configures the root logger further
    root_logger = logging.getLogger()

    # Create logs directory if it doesn't exist
    log_dir = "logs"
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass  # nosec B110

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
        except Exception:
            pass  # nosec B110
