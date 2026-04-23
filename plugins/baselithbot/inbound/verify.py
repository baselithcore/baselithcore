"""Helpers to verify webhook signatures (HMAC-SHA256 timing-safe compare)."""

from __future__ import annotations

import hashlib
import hmac


def verify_hmac_signature(
    secret: str,
    payload: bytes,
    signature: str,
    algorithm: str = "sha256",
) -> bool:
    """Return ``True`` if ``signature`` matches HMAC of ``payload`` with ``secret``.

    Args:
        secret: Shared secret bytes (UTF-8).
        payload: Raw request body bytes.
        signature: Hex-encoded signature (with or without ``sha256=`` prefix).
        algorithm: Hash algorithm name (``sha256`` default, ``sha1`` for IRC/Twitch).
    """
    if not signature:
        return False
    sig = signature.split("=", 1)[1] if "=" in signature else signature
    digest = hmac.new(secret.encode("utf-8"), payload, getattr(hashlib, algorithm)).hexdigest()
    return hmac.compare_digest(digest, sig)


__all__ = ["verify_hmac_signature"]
