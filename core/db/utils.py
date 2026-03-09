"""Database utility functions.

Common helpers for database operations, extracted to avoid code duplication.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo


def as_iso(
    value: Any,
    timezone: Optional["ZoneInfo"] = None,
) -> Optional[str]:
    """
    Convert datetime/str to ISO 8601 format.

    Args:
        value: The value to convert (datetime, date, or string)
        timezone: Optional timezone to apply for naive datetimes

    Returns:
        ISO 8601 formatted string or None if value is None
    """
    if value is None:
        return None

    if isinstance(value, datetime.datetime):
        if value.tzinfo is None and timezone is not None:
            value = value.replace(tzinfo=timezone)
        elif timezone is not None:
            value = value.astimezone(timezone)
        return value.isoformat()

    if hasattr(value, "isoformat"):
        # date objects: use isoformat directly (e.g., 2024-01-31)
        return value.isoformat()

    return str(value)


def now_iso(timezone: Optional["ZoneInfo"] = None) -> str:
    """
    Get current timestamp in ISO 8601 format.

    Args:
        timezone: Optional timezone for the timestamp

    Returns:
        Current timestamp as ISO 8601 string
    """
    now = (
        datetime.datetime.now(timezone)
        if timezone
        else datetime.datetime.now(datetime.timezone.utc)
    )
    return now.isoformat()
