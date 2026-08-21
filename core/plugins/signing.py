"""Ed25519 publisher signatures for plugins.

The self-declared ``integrity_sha256`` in a manifest detects accidental drift
and mid-transit tampering, but anyone who can modify the plugin tree can also
recompute the hash. This module adds an authenticity layer: the publisher
signs the integrity hash with an Ed25519 private key, and deployments pin the
corresponding public key(s) as a trust root.

Manifest field:   ``signature_ed25519`` — hex signature over the ASCII bytes
                  of the (lowercase hex) ``integrity_sha256`` value.
Trust roots:      ``BASELITH_PLUGIN_TRUST_ROOTS`` — comma-separated hex-encoded
                  32-byte Ed25519 public keys.
Enforcement:      ``BASELITH_REQUIRE_PLUGIN_SIGNATURES=true`` — the loader
                  refuses any plugin whose hash is unsigned or whose signature
                  does not verify against a configured trust root.

Signing tooling lives in ``scripts/sign_plugin_ed25519.py`` (keygen + sign).
``cryptography`` is imported lazily so lightweight tooling can import this
module without the full dependency stack.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TRUST_ROOTS_ENV = "BASELITH_PLUGIN_TRUST_ROOTS"
_REQUIRE_ENV = "BASELITH_REQUIRE_PLUGIN_SIGNATURES"


def generate_keypair_hex() -> tuple[str, str]:
    """Generate an Ed25519 keypair as ``(private_hex, public_hex)`` raw bytes."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    private = Ed25519PrivateKey.generate()
    private_hex = private.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    ).hex()
    public_hex = private.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    ).hex()
    return private_hex, public_hex


def sign_plugin_hash(integrity_hash_hex: str, private_key_hex: str) -> str:
    """Sign the (lowercase) integrity hash; returns the hex signature."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return private.sign(integrity_hash_hex.lower().encode("ascii")).hex()


def verify_plugin_signature(
    integrity_hash_hex: str,
    signature_hex: str | None,
    trusted_public_keys_hex: list[str],
) -> bool:
    """True when the signature verifies against ANY configured trust root.

    Malformed signatures/keys return ``False`` rather than raising: at the
    loader boundary a broken signature is a refusal, not a crash.
    """
    if not signature_hex or not trusted_public_keys_hex:
        return False
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return False
    message = integrity_hash_hex.lower().encode("ascii")
    for public_hex in trusted_public_keys_hex:
        try:
            public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
            public.verify(signature, message)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def load_trust_roots() -> list[str]:
    """Read the configured trust roots (hex public keys) from the environment."""
    raw = os.environ.get(_TRUST_ROOTS_ENV, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def is_signature_required() -> bool:
    """Whether ``BASELITH_REQUIRE_PLUGIN_SIGNATURES`` is set to a truthy value."""
    raw = os.environ.get(_REQUIRE_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def enforce_plugin_signature(
    plugin_name: str,
    integrity_hash_hex: str | None,
    signature_hex: str | None,
) -> bool:
    """Loader-side gate. True when the plugin may load.

    No-op (True) unless ``BASELITH_REQUIRE_PLUGIN_SIGNATURES`` is enabled.
    When enabled: requires a hash, a signature, at least one trust root, and
    a successful verification — each missing piece is a refusal with its own
    log line so operators can tell misconfiguration from tampering.
    """
    if not is_signature_required():
        return True
    roots = load_trust_roots()
    if not roots:
        logger.error(
            "BASELITH_REQUIRE_PLUGIN_SIGNATURES is enabled but no trust roots "
            "are configured (%s); refusing plugin %s.",
            _TRUST_ROOTS_ENV,
            plugin_name,
        )
        return False
    if not integrity_hash_hex or not signature_hex:
        logger.error(
            "Refusing plugin %s: signature enforcement is enabled but the "
            "manifest lacks %s.",
            plugin_name,
            "integrity_sha256" if not integrity_hash_hex else "signature_ed25519",
        )
        return False
    if not verify_plugin_signature(integrity_hash_hex, signature_hex, roots):
        logger.error(
            "Refusing plugin %s: signature_ed25519 does not verify against "
            "any configured trust root.",
            plugin_name,
        )
        return False
    logger.debug("Plugin %s publisher signature verified.", plugin_name)
    return True


__all__ = [
    "enforce_plugin_signature",
    "generate_keypair_hex",
    "is_signature_required",
    "load_trust_roots",
    "sign_plugin_hash",
    "verify_plugin_signature",
]
