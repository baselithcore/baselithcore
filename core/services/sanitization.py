"""
Input Sanitization Module.

Provides centralized input sanitization functions to prevent injection attacks
and ensure data safety across the application.

Usage:
    from core.services.sanitization import InputSanitizer

    sanitized_query = InputSanitizer.sanitize_query(user_input)
    sanitized_html = InputSanitizer.sanitize_html(content)
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Dict, Optional
from core.observability.logging import get_logger

logger = get_logger(__name__)


class InputSanitizer:
    """Centralized input sanitization with various strategies."""

    # Characters that should be removed or escaped in queries
    QUERY_DANGEROUS_PATTERNS = [
        r"<script[^>]*>.*?</script>",  # Script tags
        r"javascript:",  # JS protocol
        r"on\w+\s*=",  # Event handlers
        r"\x00",  # Null bytes
    ]

    # Maximum lengths for different input types
    MAX_QUERY_LENGTH = 10000
    MAX_PATH_LENGTH = 1000
    MAX_CONTENT_LENGTH = 1000000  # 1MB of text

    @classmethod
    def sanitize_query(cls, query: str, max_length: Optional[int] = None) -> str:
        """
        Sanitize user query input.

        Removes dangerous patterns and truncates to safe length.

        Args:
            query: Raw user query
            max_length: Maximum allowed length (uses default if None)

        Returns:
            Sanitized query string
        """
        if not query:
            return ""

        max_len = max_length or cls.MAX_QUERY_LENGTH

        # Strip whitespace
        result = query.strip()

        # Truncate to max length
        if len(result) > max_len:
            result = result[:max_len]
            logger.warning(f"Query truncated from {len(query)} to {max_len} chars")

        # Remove dangerous patterns
        for pattern in cls.QUERY_DANGEROUS_PATTERNS:
            result = re.sub(pattern, "", result, flags=re.IGNORECASE | re.DOTALL)

        # Remove null bytes
        result = result.replace("\x00", "")

        return result

    @classmethod
    def sanitize_html(cls, content: str, allow_markdown: bool = True) -> str:
        """
        Sanitize HTML content.

        Escapes HTML entities to prevent XSS attacks.

        Args:
            content: Raw content with potential HTML
            allow_markdown: If True, preserves certain markdown characters

        Returns:
            HTML-escaped content
        """
        if not content:
            return ""

        # Truncate extremely long content
        if len(content) > cls.MAX_CONTENT_LENGTH:
            content = content[: cls.MAX_CONTENT_LENGTH]
            logger.warning("Content truncated to max length")

        # Escape HTML entities
        result = html.escape(content)

        if allow_markdown:
            # Restore common markdown that was escaped
            # This allows markdown to be rendered while preventing HTML injection
            result = result.replace("&amp;", "&")  # Ampersands in URLs
            # Note: We don't restore < and > as they're the main XSS vectors

        return result

    @classmethod
    def sanitize_path(cls, path: str, base_path: Optional[Path] = None) -> str:
        """
        Sanitize file path input to prevent traversal attacks.

        Args:
            path: Raw path string
            base_path: Optional base path to resolve relative paths

        Returns:
            Sanitized path string

        Raises:
            ValueError: If path attempts directory traversal outside base
        """
        if not path:
            return ""

        # Remove null bytes
        path = path.replace("\x00", "")

        # Truncate
        if len(path) > cls.MAX_PATH_LENGTH:
            raise ValueError(f"Path exceeds maximum length of {cls.MAX_PATH_LENGTH}")

        # Remove protocol prefixes that could be dangerous
        for prefix in ["file://", "http://", "https://", "ftp://"]:
            if path.lower().startswith(prefix):
                path = path[len(prefix) :]

        # Resolve the path
        try:
            resolved = Path(path).resolve()
        except Exception as e:
            raise ValueError(f"Invalid path: {e}") from e

        # If base_path provided, ensure resolved path is within it
        if base_path:
            base_resolved = base_path.resolve()
            try:
                resolved.relative_to(base_resolved)
            except ValueError as e:
                raise ValueError(
                    f"Path traversal detected: {path} is outside {base_path}"
                ) from e

        return str(resolved)

    @classmethod
    def sanitize_identifier(cls, identifier: str) -> str:
        """
        Sanitize an identifier (e.g., session ID, document ID).

        Only allows alphanumeric characters, underscores, and hyphens.

        Args:
            identifier: Raw identifier

        Returns:
            Sanitized identifier
        """
        if not identifier:
            return ""

        # Only allow safe characters
        result = re.sub(r"[^a-zA-Z0-9_\-]", "", identifier)

        # Limit length
        if len(result) > 256:
            result = result[:256]

        return result

    @classmethod
    def sanitize_dict(cls, data: Dict[str, Any], max_depth: int = 5) -> Dict[str, Any]:
        """
        Recursively sanitize string values in a dictionary.

        Args:
            data: Dictionary with potential unsafe values
            max_depth: Maximum recursion depth

        Returns:
            Dictionary with sanitized string values
        """
        if max_depth <= 0:
            return {}

        result: Dict[str, Any] = {}
        for key, value in data.items():
            # Sanitize the key
            safe_key = cls.sanitize_identifier(str(key)) if key else key

            if isinstance(value, str):
                result[safe_key] = cls.sanitize_query(value)
            elif isinstance(value, dict):
                result[safe_key] = cls.sanitize_dict(value, max_depth - 1)
            elif isinstance(value, list):
                result[safe_key] = [
                    cls.sanitize_query(v) if isinstance(v, str) else v for v in value
                ]
            else:
                result[safe_key] = value

        return result


# Convenience functions for common operations
def sanitize_query(query: str) -> str:
    """
    Sanitize user query input using the InputSanitizer.

    Args:
        query: The raw query string to clean.

    Returns:
        str: The sanitized query.
    """
    return InputSanitizer.sanitize_query(query)


def sanitize_html(content: str) -> str:
    """
    Sanitize HTML content using the InputSanitizer.

    Args:
        content: The raw HTML content to clean.

    Returns:
        str: The escaped HTML content.
    """
    return InputSanitizer.sanitize_html(content)


def sanitize_path(path: str, base_path: Optional[Path] = None) -> str:
    """
    Sanitize file path using the InputSanitizer.

    Args:
        path: The path string to clean.
        base_path: Optional base directory to enforce.

    Returns:
        str: The resolved and safe path string.
    """
    return InputSanitizer.sanitize_path(path, base_path)


def sanitize_identifier(identifier: str) -> str:
    """
    Sanitize an identifier using the InputSanitizer.

    Args:
        identifier: The raw ID string to clean.

    Returns:
        str: The sanitized identifier.
    """
    return InputSanitizer.sanitize_identifier(identifier)


__all__ = [
    "InputSanitizer",
    "sanitize_query",
    "sanitize_html",
    "sanitize_path",
    "sanitize_identifier",
]
