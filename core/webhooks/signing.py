"""
HMAC-SHA256 signing for webhook payloads (Stripe-style scheme).

The signature header binds a timestamp to the exact request body so receivers
can (a) verify authenticity with the shared secret and (b) reject replays
outside a freshness window. Header format::

    X-Baselith-Signature: t=<unix_ts>,v1=<hex_hmac_sha256>

where the signed message is ``f"{t}.{body}"``. Receivers recompute the HMAC and
compare in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping

SIGNATURE_HEADER = "X-Baselith-Signature"

#: Header names (lowercase) the dispatcher owns on every delivery. An endpoint's
#: static ``headers`` may not use them: the signature is the receiver's only
#: proof of origin, the content type/framing describe the signed body, and
#: ``Host`` is the SSRF pin. Rejected at registration and, as a second line,
#: overridden at send time so a stored record can never win either.
RESERVED_DELIVERY_HEADERS: frozenset[str] = frozenset(
    {
        SIGNATURE_HEADER.lower(),
        "host",
        "content-type",
        "content-length",
        "transfer-encoding",
        "connection",
        "user-agent",
    }
)


def reserved_header_names(headers: Mapping[str, str]) -> list[str]:
    """Return the keys of ``headers`` that collide with a reserved name.

    Case-insensitive, sorted for stable error messages; empty when clean.
    """
    return sorted(k for k in headers if k.lower() in RESERVED_DELIVERY_HEADERS)


_SCHEME_VERSION = "v1"


def _compute(secret: str, timestamp: int, body: bytes) -> str:
    signed = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def build_signature_header(
    secret: str, body: bytes, *, timestamp: int | None = None
) -> str:
    """Build the ``t=...,v1=...`` signature header value for ``body``."""
    ts = timestamp if timestamp is not None else int(time.time())
    sig = _compute(secret, ts, body)
    return f"t={ts},{_SCHEME_VERSION}={sig}"


def _parse_header(header: str) -> tuple[int | None, str | None]:
    """Parse a signature header into ``(timestamp, v1_signature)``."""
    ts: int | None = None
    sig: str | None = None
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                ts = int(value)
            except ValueError:
                ts = None
        elif key == _SCHEME_VERSION:
            sig = value
    return ts, sig


def verify_signature(
    secret: str,
    body: bytes,
    header: str,
    *,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> bool:
    """Verify a webhook signature header against the body and secret.

    Args:
        secret: The endpoint's shared signing secret.
        body: The raw request body bytes (exactly as received).
        header: The received signature header value.
        tolerance_seconds: Max allowed age of the timestamp. ``0`` disables the
            freshness check.
        now: Override the current unix time (testing).

    Returns:
        ``True`` only if the signature matches and the timestamp is fresh.
    """
    ts, sig = _parse_header(header)
    if ts is None or not sig:
        return False
    if tolerance_seconds > 0:
        current = now if now is not None else int(time.time())
        if abs(current - ts) > tolerance_seconds:
            return False
    expected = _compute(secret, ts, body)
    # Constant-time comparison to avoid timing oracles.
    return hmac.compare_digest(expected, sig)
