"""Log-injection-safe rendering of untrusted values.

Values that originate outside the process — plugin manifest fields, request
headers, filenames — must never reach a log line verbatim: a newline lets a
caller forge an entire additional log entry, and terminal escapes can rewrite
what an operator sees when tailing the file.

:func:`sanitize_log_value` escapes every non-printable character (so the
evidence survives, unlike a plain strip) and caps the length, keeping a single
log record to a single line.

Stdlib only, by design: the plugin integrity/signature gates import this before
the observability stack is available.
"""

from __future__ import annotations

DEFAULT_MAX_LENGTH = 200
_TRUNCATION_SUFFIX = "...[truncated]"


def sanitize_log_value(value: object, *, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """Return ``value`` as a single-line, printable string safe to log.

    Args:
        value: Any object; non-strings are rendered with ``str()``.
        max_length: Maximum length of the returned string, truncation marker
            included. Values below 1 are treated as 1.

    Returns:
        str: The escaped value — control characters (newlines, carriage
        returns, ANSI escapes, NUL) become their ``\\xNN`` / ``\\uNNNN``
        representation, and over-long values are truncated.
    """
    text = value if isinstance(value, str) else str(value)
    escaped = "".join(char if char.isprintable() else _escape(char) for char in text)
    limit = max(1, max_length)
    if len(escaped) <= limit:
        return escaped
    if limit <= len(_TRUNCATION_SUFFIX):
        return escaped[:limit]
    return escaped[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def _escape(char: str) -> str:
    code = ord(char)
    if code <= 0xFF:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


__all__ = ["DEFAULT_MAX_LENGTH", "sanitize_log_value"]
