"""Example Plugin Utilities.

Helper functions and common utilities.
"""

from core.observability.logging import get_logger
from typing import Optional

logger = get_logger(__name__)


def format_item_name(name: str) -> str:
    """Format an item name (example utility function)."""
    return name.strip().title()


def validate_input(data: Optional[str]) -> bool:
    """Validate input string."""
    if not data:
        return False
    return len(data.strip()) > 0
