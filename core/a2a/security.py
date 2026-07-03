"""
A2A request signing.

Optional HMAC-SHA256 authentication for agent-to-agent HTTP traffic.

When the environment variable ``BASELITH_A2A_SHARED_SECRET`` is set, the
:class:`~core.a2a.client.A2AClient` signs every outgoing request body and the
A2A router rejects incoming requests whose signature is missing or invalid.
Without the secret the protocol behaves exactly as before (unauthenticated),
preserving backward compatibility for single-process and trusted-mesh
deployments — but a CRITICAL log is emitted in production so the posture is
never silent.

Wire format (HTTP headers):

- ``X-A2A-Timestamp``: unix epoch seconds at signing time.
- ``X-A2A-Signature``: ``sha256=<hex>`` where ``hex`` is
  ``HMAC_SHA256(secret, timestamp + "." + raw_body)``.

The timestamp is bound into the MAC and verified against a configurable skew
window, which limits replay of captured requests to that window. Deployments
that need strict single-use semantics should additionally enforce idempotent
message IDs at the application layer.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from pydantic import SecretStr

from core.observability.logging import get_logger

logger = get_logger(__name__)

TIMESTAMP_HEADER = "X-A2A-Timestamp"
SIGNATURE_HEADER = "X-A2A-Signature"
_SIGNATURE_PREFIX = "sha256="

#: Maximum accepted clock skew between signer and verifier, in seconds.
DEFAULT_MAX_SKEW_SECONDS = 300

_ENV_SECRET = "BASELITH_A2A_SHARED_SECRET"
_ENV_ALLOW_UNAUTH = "BASELITH_A2A_ALLOW_UNAUTHENTICATED"
_warned_unauthenticated = False


def get_a2a_shared_secret() -> SecretStr | None:
    """Return the configured A2A shared secret, or None when not set."""
    raw = os.environ.get(_ENV_SECRET, "").strip()
    return SecretStr(raw) if raw else None


def _is_production() -> bool:
    env = (
        (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development")
        .strip()
        .lower()
    )
    return env == "production"


def unauthenticated_a2a_allowed() -> bool:
    """Whether an unsigned A2A request may be processed.

    Fail-closed in production: when no shared secret is configured, unsigned
    requests are refused unless the operator explicitly opts in with
    ``BASELITH_A2A_ALLOW_UNAUTHENTICATED=true``. Outside production the previous
    (unauthenticated) behavior is preserved for trusted-mesh / local use.
    """
    if not _is_production():
        return True
    raw = os.environ.get(_ENV_ALLOW_UNAUTH, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def warn_if_unauthenticated_in_production() -> None:
    """Emit a one-shot CRITICAL log when A2A runs unsigned in production."""
    global _warned_unauthenticated
    if _warned_unauthenticated or get_a2a_shared_secret() is not None:
        return
    if _is_production():
        logger.critical(
            "A2A endpoints are UNAUTHENTICATED in production. Any peer that "
            "can reach the endpoint can invoke this agent. Set "
            "BASELITH_A2A_SHARED_SECRET on all peers to enable HMAC signing "
            "(or BASELITH_A2A_ALLOW_UNAUTHENTICATED=true to explicitly opt in)."
        )
    _warned_unauthenticated = True


def _compute_signature(body: bytes, timestamp: str, secret: str) -> str:
    mac = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + body,
        hashlib.sha256,
    )
    return _SIGNATURE_PREFIX + mac.hexdigest()


def build_signature_headers(body: bytes, secret: SecretStr) -> dict[str, str]:
    """Build the signature headers for an outgoing A2A request body."""
    timestamp = str(int(time.time()))
    signature = _compute_signature(body, timestamp, secret.get_secret_value())
    return {TIMESTAMP_HEADER: timestamp, SIGNATURE_HEADER: signature}


def verify_signature(
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    secret: SecretStr,
    *,
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
) -> bool:
    """Verify an incoming A2A request against the shared secret.

    Args:
        body: Raw request body bytes, exactly as received.
        timestamp_header: Value of ``X-A2A-Timestamp``, or None if absent.
        signature_header: Value of ``X-A2A-Signature``, or None if absent.
        secret: The shared secret.
        max_skew_seconds: Accepted clock skew / replay window.

    Returns:
        True when the signature is present, fresh, and valid.
    """
    if not timestamp_header or not signature_header:
        return False
    try:
        timestamp = int(timestamp_header)
    except ValueError:
        return False
    if abs(time.time() - timestamp) > max_skew_seconds:
        return False
    expected = _compute_signature(body, timestamp_header, secret.get_secret_value())
    return hmac.compare_digest(expected, signature_header)
