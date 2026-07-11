"""Sensitive-data redaction for log entries.

Extracted from ``core.observability.logging`` (module size cap); all names
are re-exported there for backward compatibility. The structlog processor
:func:`redact_sensitive` runs on every log entry (structlog pipeline AND the
foreign-log pre-chain), masking secrets by key and by value.
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any

from core.config import get_app_config

_REDACTED = "[REDACTED]"

# Substrings that mark a structured field (or log kwarg) as a secret; the
# value is replaced wholesale. Mirrors the Sentry scrubber's key set.
_SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "session",
    "jwt",
    "bearer",
    "credential",
    "private_key",
    "access_key",
)
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-admin-token",
        "proxy-authorization",
    }
)

_EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# marker (:|=|whitespace) value  →  redact the value, keep the marker.
_CREDENTIALS_REGEX = re.compile(
    r"(?i)(api[-_]?key|authorization|bearer|token|secret|password|passwd)"
    r"(?P<sep>[:=\s]+)(?P<val>[^\s,;]+)"
)


def _key_is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _mask_email(match: re.Match[str]) -> str:
    email = match.group(0)
    try:
        user, domain = email.split("@")
    except ValueError:
        return "[EMAIL_REDACTED]"
    masked = (user[:3] + "***") if len(user) > 3 else (user[0] + "***")
    return f"{masked}@{domain}"


def _mask_text(value: str) -> str:
    value = _EMAIL_REGEX.sub(_mask_email, value)
    value = _CREDENTIALS_REGEX.sub(r"\1\g<sep>[REDACTED]", value)
    return value


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _mask_text(value)
    if isinstance(value, dict):
        return {
            k: (
                _REDACTED
                if isinstance(k, str) and _key_is_sensitive(k)
                else _redact_value(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def redact_url_credentials(url: str) -> str:
    """Return ``url`` with any embedded ``user:password@`` userinfo removed.

    Connection strings (``redis://:pass@host``, ``postgres://u:p@host``) must
    never be logged verbatim — the password would land in logs/Sentry. Logs the
    scheme/host/port/path only.
    """
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
    except Exception:
        return url
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: strip secrets from every log entry.

    Two complementary passes, so both structured kwargs and raw message strings
    are covered (the previous stdlib-filter approach saw neither structlog
    kwargs nor JSON-rendered fields):

    1. **By key** — any field whose name marks it a secret (``token``,
       ``authorization``, ``password``…) has its value replaced with
       ``[REDACTED]``, recursively into nested dicts/lists.
    2. **By value** — remaining string values (including the message ``event``)
       have emails masked and inline ``key=secret`` / ``Bearer <t>`` patterns
       redacted.

    Gated on ``log_masking_enabled`` (default on). Installed in both the
    structlog pipeline and the foreign-log pre-chain by ``configure_logging``,
    so it applies on the FastAPI/uvicorn path — not only the MCP server.
    """
    try:
        if not getattr(get_app_config(), "log_masking_enabled", True):
            return event_dict
    except Exception:
        pass

    for key in list(event_dict.keys()):
        if isinstance(key, str) and key != "event" and _key_is_sensitive(key):
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


__all__ = ["redact_sensitive", "redact_url_credentials"]
