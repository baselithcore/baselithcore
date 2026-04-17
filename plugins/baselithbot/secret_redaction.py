"""Redact known sensitive keys from arbitrary log/audit payloads.

Used by ``AuditLogger`` and ``UsageLedger`` to keep secrets out of
JSON-Lines audit files and structured logs.
"""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY_PATTERNS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "bot_token",
    "webhook_url",
    "auth_token",
    "private_key",
    "session_cookie",
    "authorization",
    "bearer",
)


_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]+)", re.IGNORECASE)
_LONG_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9_\-]{32,})")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(pattern in lowered for pattern in _SENSITIVE_KEY_PATTERNS)


def _redact_string(value: str, max_keep: int = 4) -> str:
    redacted = _BEARER_RE.sub(lambda m: f"{m.group(1)}<redacted>", value)
    return _LONG_TOKEN_RE.sub(
        lambda m: f"<redacted:{m.group(1)[:max_keep]}…>",
        redacted,
    )


def redact_payload(payload: Any, *, max_depth: int = 6) -> Any:
    """Return a deep copy of ``payload`` with sensitive fields masked."""
    if max_depth <= 0:
        return "<truncated>"
    if isinstance(payload, dict):
        return {
            k: (
                "<redacted>"
                if _is_sensitive_key(k)
                else redact_payload(v, max_depth=max_depth - 1)
            )
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [redact_payload(item, max_depth=max_depth - 1) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_payload(item, max_depth=max_depth - 1) for item in payload)
    if isinstance(payload, str):
        return _redact_string(payload)
    return payload


__all__ = ["redact_payload"]
