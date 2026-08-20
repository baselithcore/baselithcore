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
- ``X-A2A-Nonce``: single-use random token, bound into the MAC.
- ``X-A2A-Signature``: ``sha256=<hex>`` where ``hex`` is
  ``HMAC_SHA256(secret, timestamp + "." + nonce + "." + raw_body)`` when a
  nonce is present, or the legacy ``HMAC_SHA256(secret, timestamp + "." +
  raw_body)`` when it is not.

The timestamp bounds replay to the skew window; the nonce closes it entirely —
the verifier records each nonce for the window's duration and rejects repeats,
so a captured signed request cannot be re-sent even inside the window. The
nonce cannot be stripped for a downgrade: it is bound inside the MAC, so
removing the header invalidates the signature. The nonce is REQUIRED by
default: a nonce-less request, even with a valid legacy MAC, is replayable for
the whole skew window, so it is refused unless the operator explicitly opts
into the deprecated compatibility window with
``BASELITH_A2A_ALLOW_LEGACY_NONCELESS=true`` while older peers are upgraded.
The nonce ledger is per-process; multi-replica deployments that need
cross-replica single-use should front A2A with a shared store (the window is
short, so the residual per-replica exposure is one skew window per replica).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid

from pydantic import SecretStr

from core.observability.logging import get_logger

logger = get_logger(__name__)

TIMESTAMP_HEADER = "X-A2A-Timestamp"
NONCE_HEADER = "X-A2A-Nonce"
SIGNATURE_HEADER = "X-A2A-Signature"
_SIGNATURE_PREFIX = "sha256="

#: Maximum accepted clock skew between signer and verifier, in seconds.
DEFAULT_MAX_SKEW_SECONDS = 300


class _NonceLedger:
    """Process-local single-use ledger with lazy TTL pruning."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def register_once(self, nonce: str, ttl_seconds: float) -> bool:
        """Record ``nonce``; False when it was already seen (replay)."""
        now = time.time()
        # Lazy prune: the ledger is bounded by the request rate × window.
        if len(self._seen) > 10_000:
            self._seen = {n: exp for n, exp in self._seen.items() if exp > now}
        if self._seen.get(nonce, 0.0) > now:
            return False
        self._seen[nonce] = now + ttl_seconds
        return True


_nonce_ledger = _NonceLedger()

_ENV_SECRET = "BASELITH_A2A_SHARED_SECRET"
_ENV_ALLOW_UNAUTH = "BASELITH_A2A_ALLOW_UNAUTHENTICATED"
_ENV_ALLOW_LEGACY_NONCELESS = "BASELITH_A2A_ALLOW_LEGACY_NONCELESS"
_warned_unauthenticated = False
_warned_legacy_nonceless = False


def legacy_nonceless_allowed() -> bool:
    """Whether a signed request without a nonce may still verify.

    Deprecated compatibility window for peers predating the nonce: their MAC is
    valid but replayable for the whole skew window, so acceptance requires an
    explicit operator opt-in. Enabling it logs CRITICAL once so the weakened
    posture is never silent.
    """
    raw = os.environ.get(_ENV_ALLOW_LEGACY_NONCELESS, "").strip().lower()
    allowed = raw in ("1", "true", "yes", "on")
    global _warned_legacy_nonceless
    if allowed and not _warned_legacy_nonceless:
        logger.critical(
            "A2A accepts DEPRECATED nonce-less signatures "
            "(BASELITH_A2A_ALLOW_LEGACY_NONCELESS=true): captured requests are "
            "replayable within the skew window. Upgrade all peers and remove "
            "the opt-in."
        )
        _warned_legacy_nonceless = True
    return allowed


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


def _compute_signature(
    body: bytes, timestamp: str, secret: str, nonce: str | None = None
) -> str:
    message = timestamp.encode("ascii")
    if nonce:
        message += b"." + nonce.encode("ascii")
    message += b"." + body
    mac = hmac.new(secret.encode("utf-8"), message, hashlib.sha256)
    return _SIGNATURE_PREFIX + mac.hexdigest()


def build_signature_headers(body: bytes, secret: SecretStr) -> dict[str, str]:
    """Build the signature headers for an outgoing A2A request body.

    Every outgoing request carries a fresh single-use nonce bound into the
    MAC, so the receiving peer can reject replays of captured requests.
    """
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = _compute_signature(
        body, timestamp, secret.get_secret_value(), nonce=nonce
    )
    return {
        TIMESTAMP_HEADER: timestamp,
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: signature,
    }


def verify_signature(
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    secret: SecretStr,
    *,
    nonce_header: str | None = None,
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
) -> bool:
    """Verify an incoming A2A request against the shared secret.

    Args:
        body: Raw request body bytes, exactly as received.
        timestamp_header: Value of ``X-A2A-Timestamp``, or None if absent.
        signature_header: Value of ``X-A2A-Signature``, or None if absent.
        secret: The shared secret.
        nonce_header: Value of ``X-A2A-Nonce``. Bound into the MAC and
            enforced single-use within the skew window. Required by default;
            absent (legacy peer) is accepted only under the deprecated
            ``BASELITH_A2A_ALLOW_LEGACY_NONCELESS=true`` opt-in.
        max_skew_seconds: Accepted clock skew / replay window.

    Returns:
        True when the signature is present, fresh, valid, and not a replay.
    """
    if not timestamp_header or not signature_header:
        return False
    if not nonce_header and not legacy_nonceless_allowed():
        logger.warning(
            "Rejected A2A request: missing nonce (legacy nonce-less signatures "
            "are disabled by default)"
        )
        return False
    try:
        timestamp = int(timestamp_header)
    except ValueError:
        return False
    if abs(time.time() - timestamp) > max_skew_seconds:
        return False
    expected = _compute_signature(
        body, timestamp_header, secret.get_secret_value(), nonce=nonce_header
    )
    if not hmac.compare_digest(expected, signature_header):
        return False
    if nonce_header:
        # After the MAC checks out: a forged nonce can't reach this point, so
        # a repeat here is a genuine replay of a captured request. Keep the
        # entry alive for both skew directions plus slack.
        if not _nonce_ledger.register_once(
            nonce_header, ttl_seconds=max_skew_seconds * 2 + 1
        ):
            logger.warning("Rejected A2A request: nonce replay detected")
            return False
    return True
