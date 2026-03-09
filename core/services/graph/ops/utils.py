"""Utility functions for graph operations."""

from datetime import datetime, timezone


def current_timestamp() -> str:
    """
    Generate current UTC timestamp in ISO format.

    Returns:
        ISO-formatted timestamp string
    """
    return datetime.now(timezone.utc).isoformat()
