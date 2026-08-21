"""Fast digests for high-entropy credentials.

API keys, JWTs and session tokens are indexed and cached by digest so the raw
value never sits in a dictionary key, a cache entry or a rate-limit bucket
name. SHA-256 is the right primitive for that: the inputs are random tokens,
not human-chosen passwords, and the lookup runs on every authenticated
request, where a password KDF would add latency and buy nothing.

Password *storage* is a different problem and lives elsewhere — see
``core/auth`` for the argon2/bcrypt paths that handle operator credentials.
"""

from __future__ import annotations

import hashlib


def credential_digest(data: bytes) -> str:
    """Hex SHA-256 of an opaque byte string.

    Args:
        data: Encoded credential material to index by.

    Returns:
        str: Lowercase hex digest, suitable as a lookup or cache key.
    """
    # codeql[py/weak-sensitive-data-hashing] — index over random tokens, not
    # password storage; see the module docstring.
    return hashlib.sha256(data).hexdigest()


__all__ = ["credential_digest"]
