"""Digest primitives for the tamper-evident audit chain.

Split out of :mod:`core.observability.audit_chain` (module size cap) and kept
free of storage concerns: nothing here opens a file or a connection, so the
chain arithmetic stays directly testable and reusable by a verifier that only
has rows. ``audit_chain`` re-exports every name, so the historical import path
is unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from pydantic import SecretStr

#: ``prev_hash`` of the very first record in a fresh chain.
GENESIS_HASH = "0" * 64

#: Environment variable behind ``AuditConfig.chain_require_key``.
REQUIRE_KEY_ENV = "AUDIT_CHAIN_REQUIRE_KEY"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def require_chain_key_from_env() -> bool:
    """Read ``AUDIT_CHAIN_REQUIRE_KEY`` without going through pydantic.

    The settings object is exactly what fails to build when the requirement is
    set and the key is not, so the guard cannot depend on it. This is the
    last-resort read that keeps a broken configuration fail-closed.
    """
    return (os.getenv(REQUIRE_KEY_ENV) or "").strip().lower() in _TRUTHY


class AuditChainKeyError(RuntimeError):
    """Raised when a keyed chain is mandated but no key is available."""


def coerce_chain_key(key: SecretStr | str | bytes | None) -> bytes | None:
    """Normalise a chain key to raw bytes, or ``None`` when there is none.

    Accepts the :class:`~pydantic.SecretStr` the configuration stores, a plain
    string, or bytes. A blank value — empty *or* whitespace-only — is treated
    as "no key": ``AUDIT_CHAIN_HMAC_KEY="   "`` is an operator who has not set
    a key, and keying the chain with three spaces would be worse than refusing,
    because it looks configured. A key with meaningful content keeps its
    surrounding whitespace verbatim, since stripping it would silently
    invalidate an existing chain.
    """
    if key is None:
        return None
    if isinstance(key, SecretStr):
        raw: str | bytes = key.get_secret_value()
    else:
        raw = key
    if isinstance(raw, str):
        if not raw.strip():
            return None
        raw = raw.encode("utf-8")
    elif not raw.strip():
        return None
    return raw or None


def compute_entry_hash(
    prev_hash: str,
    payload: dict[str, Any],
    key: SecretStr | str | bytes | None = None,
) -> str:
    """Return the chain digest of ``prev_hash || canonical_json(payload)``.

    The payload is serialized with sorted keys and no whitespace so the digest
    depends only on the data, never on dict ordering or formatting.

    Args:
        prev_hash: The predecessor record's ``entry_hash``.
        payload: The canonical record content being chained.
        key: HMAC key. With a key the digest is ``HMAC-SHA256``; without one it
            is the historical plain ``SHA-256``.

    Returns:
        The hex digest to store as this record's ``entry_hash``.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    message = (prev_hash + canonical).encode("utf-8")
    secret = coerce_chain_key(key)
    if secret is None:
        return hashlib.sha256(message).hexdigest()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


__all__ = [
    "GENESIS_HASH",
    "REQUIRE_KEY_ENV",
    "AuditChainKeyError",
    "coerce_chain_key",
    "compute_entry_hash",
    "require_chain_key_from_env",
]
