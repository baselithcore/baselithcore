"""Runtime log-level control, split out of :mod:`baselith.core.observability.logging`
to keep that module under the file-size cap.
"""

from __future__ import annotations

import logging

#: Names :func:`set_log_level` accepts — the stdlib level names, nothing else.
_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def set_log_level(level: str, logger_name: str | None = None) -> str:
    """Change a logger's level at runtime — no restart required.

    ``structlog.stdlib.filter_by_level`` (in the processor chain built by
    ``configure_logging``) checks the underlying stdlib logger's *current*
    effective level on every call, so a plain ``Logger.setLevel`` already
    takes effect on the next log call with no re-configuration. This only
    validates the name and applies it — the mechanism it relies on is not
    new.

    Args:
        level: One of ``DEBUG``/``INFO``/``WARNING``/``ERROR``/``CRITICAL``
            (case-insensitive).
        logger_name: Logger to adjust; ``None`` means the root logger, which
            is what every unconfigured child logger inherits from.

    Returns:
        The normalized (upper-case) level name that was applied.

    Raises:
        ValueError: ``level`` is not one of the five names above.
    """
    normalized = level.strip().upper()
    if normalized not in _VALID_LOG_LEVELS:
        raise ValueError(f"unknown log level: {level!r}")
    logging.getLogger(logger_name).setLevel(normalized)
    return normalized


def get_log_level(logger_name: str | None = None) -> str:
    """The level currently in force for a logger (root by default)."""
    return logging.getLevelName(logging.getLogger(logger_name).getEffectiveLevel())


__all__ = ["get_log_level", "set_log_level"]
