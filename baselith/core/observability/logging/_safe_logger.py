"""Stdlib-logging fallback wrapper, split out of
:mod:`baselith.core.observability.logging` to keep that module under the
file-size cap.
"""

from __future__ import annotations

import logging
from typing import Any

from baselith.core.observability.redaction import _REDACTED, _key_is_sensitive
from baselith.core.utils.logsafe import sanitize_log_value

#: Generous cap for a single rendered field: long enough for a stack-trace
#: line or a payload excerpt, short enough that one field cannot flood the log.
_MAX_LOGGED_CHARS = 2000


class SafeLogger:
    """
    Fallback wrapper for standard logging when structlog is missing.

    Ensures that modern kwarg-based logging (e.g. log.info("msg", key="val"))
    remains functional and readable in standard text logs.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _format(self, msg: Any, **kwargs: Any) -> str:
        """Render message + kwargs as one safe line.

        This path bypasses the structlog processors, so it has to do their two
        jobs itself: values whose key marks them a secret are replaced, and
        every rendered value is escaped — a kwarg carrying a newline would
        otherwise forge a second log entry.
        """
        safe_msg = sanitize_log_value(msg, max_length=_MAX_LOGGED_CHARS)
        if not kwargs:
            return safe_msg
        pairs = " ".join(
            f"{k}="
            + (
                _REDACTED
                if _key_is_sensitive(str(k))
                else sanitize_log_value(v, max_length=_MAX_LOGGED_CHARS)
            )
            for k, v in kwargs.items()
        )
        return f"{safe_msg} [{pairs}]"

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


__all__ = ["SafeLogger"]
