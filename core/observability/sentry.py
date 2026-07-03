"""
Sentry Error Tracking Integration.

Initializes the Sentry SDK for capturing unhandled exceptions
and monitoring performance if a SENTRY_DSN is provided in the configuration.
"""

from __future__ import annotations

from typing import Any

import sentry_sdk
from pydantic import SecretStr

from core.config import get_app_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-admin-token",
        "proxy-authorization",
    }
)
_SENSITIVE_VALUE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "session",
        "jwt",
    }
)
_REDACTED = "[REDACTED]"


def _scrub_mapping(payload: Any) -> None:
    """In-place scrub of dict-like payloads. Replaces values with ``[REDACTED]``."""
    if not isinstance(payload, dict):
        return
    for key, value in list(payload.items()):
        lowered = key.lower() if isinstance(key, str) else ""
        if lowered in _SENSITIVE_HEADER_NAMES or any(
            marker in lowered for marker in _SENSITIVE_VALUE_KEYS
        ):
            payload[key] = _REDACTED
        elif isinstance(value, dict):
            _scrub_mapping(value)
        elif isinstance(value, list):
            for item in value:
                _scrub_mapping(item)


def _before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Strip credentials/tokens from the event before transmission to Sentry."""
    request = event.get("request")
    if isinstance(request, dict):
        _scrub_mapping(request.get("headers"))
        _scrub_mapping(request.get("cookies"))
        _scrub_mapping(request.get("data"))
        _scrub_mapping(request.get("query_string"))

    contexts = event.get("contexts")
    if isinstance(contexts, dict):
        _scrub_mapping(contexts)

    extra = event.get("extra")
    if isinstance(extra, dict):
        _scrub_mapping(extra)

    for thread in event.get("threads", {}).get("values", []) or []:
        for frame in thread.get("stacktrace", {}).get("frames", []) or []:
            _scrub_mapping(frame.get("vars"))

    for exc in event.get("exception", {}).get("values", []) or []:
        for frame in exc.get("stacktrace", {}).get("frames", []) or []:
            _scrub_mapping(frame.get("vars"))

    return event


def init_sentry() -> None:
    """
    Initialize Sentry SDK for error and performance tracking.
    This should be called early in the application startup phase.
    """
    config = get_app_config()

    sentry_dsn = getattr(config, "sentry_dsn", None)
    # sentry_dsn is a SecretStr (or None); unwrap only at the SDK boundary.
    dsn_value = (
        sentry_dsn.get_secret_value()
        if isinstance(sentry_dsn, SecretStr)
        else sentry_dsn
    )

    if dsn_value:
        try:
            sentry_sdk.init(
                dsn=dsn_value,
                traces_sample_rate=getattr(config, "sentry_traces_sample_rate", 0.1),
                profiles_sample_rate=getattr(
                    config, "sentry_profiles_sample_rate", 0.1
                ),
                send_default_pii=False,
                before_send=_before_send,  # type: ignore[arg-type]
            )
            logger.info("Sentry SDK initialized successfully for error tracking.")
        except Exception as e:
            logger.error(f"Failed to initialize Sentry SDK: {e}", exc_info=True)
    else:
        logger.debug("SENTRY_DSN not configured. Sentry tracking is disabled.")
