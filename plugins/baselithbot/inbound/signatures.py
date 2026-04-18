"""Per-channel inbound webhook signature verifiers.

Each function returns ``True`` iff the signature header on the request body
matches the expected HMAC for the channel. All comparisons use
``hmac.compare_digest`` to avoid timing attacks.
"""

from __future__ import annotations

import hashlib
import hmac


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify a Slack ``X-Slack-Signature`` header (v0)."""
    if not signature.startswith("v0="):
        return False
    base = f"v0:{timestamp}:".encode("utf-8") + body
    digest = hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)


def verify_github_signature(
    secret: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify a GitHub ``X-Hub-Signature-256`` header."""
    if not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, signature)


def verify_telegram_secret_token(
    expected_token: str,
    received_token: str | None,
) -> bool:
    """Verify the ``X-Telegram-Bot-Api-Secret-Token`` header."""
    if not received_token:
        return False
    return hmac.compare_digest(expected_token, received_token)


def verify_stripe_signature(
    secret: str,
    body: bytes,
    signature_header: str,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify a Stripe-style ``Stripe-Signature`` (``t=...,v1=...``) header."""
    import time as _time

    parts: dict[str, str] = {}
    for chunk in signature_header.split(","):
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        parts[k.strip()] = v.strip()
    timestamp = parts.get("t", "")
    sig = parts.get("v1", "")
    if not (timestamp and sig):
        return False
    try:
        ts_int = int(timestamp)
    except ValueError:
        return False
    if abs(_time.time() - ts_int) > tolerance_seconds:
        return False
    payload = f"{timestamp}.".encode("utf-8") + body
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def verify_discord_signature(
    public_key_hex: str,
    timestamp: str,
    body: bytes,
    signature_hex: str,
) -> bool:
    """Verify a Discord ``X-Signature-Ed25519`` header.

    Discord signs interactions with Ed25519 over ``timestamp || body``. The
    public key is shared with the bot at application setup.
    """
    if not (public_key_hex and timestamp and signature_hex):
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError:
        return False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return False
    try:
        public_key.verify(signature, timestamp.encode("utf-8") + body)
    except InvalidSignature:
        return False
    return True


__all__ = [
    "verify_slack_signature",
    "verify_github_signature",
    "verify_telegram_secret_token",
    "verify_stripe_signature",
    "verify_discord_signature",
]
