"""Ed25519 publisher signatures for plugins.

The self-declared ``integrity_sha256`` in a manifest detects accidental drift
and mid-transit tampering, but anyone who can modify the plugin tree can also
recompute the hash. This module adds an authenticity layer: the publisher
signs the integrity hash with an Ed25519 private key, and deployments pin the
corresponding public key(s) as a trust root.

Manifest field:   ``signature_ed25519`` — hex signature over the ASCII bytes
                  of the (lowercase hex) ``integrity_sha256`` value. From hash
                  surface V5 that hash covers the manifest itself, so the
                  signature now also attests the plugin's declared permissions,
                  dependencies and version floor.
Trust store:      ``BASELITH_PLUGIN_TRUST_STORE`` — path to a JSON file of
                  ``{key_id, public_key_hex, not_after, revoked}`` entries.
Trust roots:      ``BASELITH_PLUGIN_TRUST_ROOTS`` — the legacy form:
                  comma-separated hex-encoded 32-byte Ed25519 public keys, with
                  no identity, no expiry and no way to revoke. Still honoured,
                  and merged with the store; a store entry for the same key
                  wins, so revoking a key works even while it is still listed
                  in the env var.
Enforcement:      ``BASELITH_REQUIRE_PLUGIN_SIGNATURES=true`` — the loader
                  refuses any plugin whose hash is unsigned or whose signature
                  does not verify against a *usable* trust root (neither
                  revoked nor expired).

Signing tooling lives in ``scripts/sign_plugin_ed25519.py`` (keygen + sign) and
``scripts/sign_changed_plugins.py`` (re-sign a tree in place).
``cryptography`` is imported lazily so lightweight tooling can import this
module without the full dependency stack.
"""

from __future__ import annotations

import logging
import os

from core.plugins._trust_store import (
    _TRUST_STORE_ENV,
    TrustedKey,
    load_trust_store,
)

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
    public_hex = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return private_hex, public_hex


def sign_message(message: bytes, private_key_hex: str) -> str:
    """Sign ``message`` with a raw Ed25519 private key; returns the hex signature."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    signature: bytes = private.sign(message)
    return signature.hex()


def verify_message(
    message: bytes,
    signature_hex: str | None,
    trusted_public_keys_hex: list[str],
) -> bool:
    """True when ``signature_hex`` over ``message`` verifies against ANY key.

    Malformed signatures and keys return ``False`` rather than raising.
    """
    if not signature_hex or not trusted_public_keys_hex:
        return False
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return False
    for public_hex in trusted_public_keys_hex:
        try:
            public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
            public.verify(signature, message)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def sign_plugin_hash(integrity_hash_hex: str, private_key_hex: str) -> str:
    """Sign the (lowercase) integrity hash; returns the hex signature."""
    return sign_message(integrity_hash_hex.lower().encode("ascii"), private_key_hex)


def verify_plugin_signature(
    integrity_hash_hex: str,
    signature_hex: str | None,
    trusted_public_keys_hex: list[str],
) -> bool:
    """True when the signature verifies against ANY supplied public key.

    The caller decides which keys are eligible — :func:`load_trust_roots`
    already filters out revoked and expired ones. Malformed signatures/keys
    return ``False`` rather than raising: at the loader boundary a broken
    signature is a refusal, not a crash.
    """
    return verify_message(
        integrity_hash_hex.lower().encode("ascii"),
        signature_hex,
        trusted_public_keys_hex,
    )


def _env_trust_roots() -> list[str]:
    """Hex public keys from the legacy comma-separated env var."""
    raw = os.environ.get(_TRUST_ROOTS_ENV, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def load_trusted_keys() -> list[TrustedKey]:
    """Every configured publisher key, usable or not, env and store merged.

    Env-var roots come first and carry no identity, expiry or revocation flag.
    A store entry for the same public key replaces the env-derived one in
    place, so revoking or expiring a key in the store takes effect even while
    the key is still listed in ``BASELITH_PLUGIN_TRUST_ROOTS``.

    Returns:
        The merged list, in env-then-store order.
    """
    merged: dict[str, TrustedKey] = {}
    for public_hex in _env_trust_roots():
        normalised = public_hex.lower()
        merged[normalised] = TrustedKey(
            key_id=f"env:{normalised[:16]}", public_key_hex=public_hex
        )
    for key in load_trust_store():
        merged[key.public_key_hex.lower()] = key
    return list(merged.values())


def load_trust_roots(plugin_name: str | None = None) -> list[str]:
    """The hex public keys a plugin signature may currently verify against.

    Args:
        plugin_name: When given, keys whose ``plugins`` scope excludes it are
            left out; ``None`` returns every usable key.

    Returns:
        Usable keys only — revoked and expired entries are filtered out, so a
        caller cannot accidentally trust one.
    """
    return [
        key.public_key_hex
        for key in load_trusted_keys()
        if key.is_usable and key.covers(plugin_name)
    ]


def is_signature_required() -> bool:
    """Whether ``BASELITH_REQUIRE_PLUGIN_SIGNATURES`` is set to a truthy value."""
    raw = os.environ.get(_REQUIRE_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _report_rejected_signer(
    safe_name: str,
    integrity_hash_hex: str,
    signature_hex: str,
    rejected: list[TrustedKey],
) -> bool:
    """Name the revoked/expired key that produced a signature, if any.

    A signature that verifies against a key the deployment has retired is a
    very different incident from a signature nobody can place — one is a
    revocation doing its job, the other is tampering or a missing trust root.

    Returns:
        ``True`` when a rejected key matched (and was logged).
    """
    for key in rejected:
        if verify_plugin_signature(
            integrity_hash_hex, signature_hex, [key.public_key_hex]
        ):
            logger.error(
                "Refusing plugin %s: signature_ed25519 was produced by trust "
                "store key %s, which is %s.",
                safe_name,
                key.key_id,
                key.rejection_reason(),
            )
            return True
    return False


def enforce_plugin_signature(
    plugin_name: str,
    integrity_hash_hex: str | None,
    signature_hex: str | None,
) -> bool:
    """Loader-side gate. True when the plugin may load.

    No-op (True) unless ``BASELITH_REQUIRE_PLUGIN_SIGNATURES`` is enabled.
    When enabled: requires a hash, a signature, at least one usable trust root,
    and a successful verification — each missing piece is a refusal with its
    own log line so operators can tell misconfiguration from revocation from
    tampering.
    """
    if not is_signature_required():
        return True
    # The name comes from the plugin's own manifest: escape it before it
    # reaches a log line so a crafted name cannot forge extra log entries.
    # Imported lazily to keep this module importable by lightweight tooling.
    from core.utils.logsafe import sanitize_log_value

    safe_name = sanitize_log_value(plugin_name)
    configured = load_trusted_keys()
    usable = [
        key.public_key_hex
        for key in configured
        if key.is_usable and key.covers(plugin_name)
    ]
    rejected = [key for key in configured if not key.is_usable]
    out_of_scope = [
        key for key in configured if key.is_usable and not key.covers(plugin_name)
    ]
    if not configured:
        logger.error(
            "BASELITH_REQUIRE_PLUGIN_SIGNATURES is enabled but no trust roots "
            "are configured (%s or %s); refusing plugin %s.",
            _TRUST_STORE_ENV,
            _TRUST_ROOTS_ENV,
            safe_name,
        )
        return False
    if not integrity_hash_hex or not signature_hex:
        logger.error(
            "Refusing plugin %s: signature enforcement is enabled but the "
            "manifest lacks %s.",
            safe_name,
            "integrity_sha256" if not integrity_hash_hex else "signature_ed25519",
        )
        return False
    if verify_plugin_signature(integrity_hash_hex, signature_hex, usable):
        logger.debug("Plugin %s publisher signature verified.", safe_name)
        return True
    if _report_rejected_signer(safe_name, integrity_hash_hex, signature_hex, rejected):
        return False
    for key in out_of_scope:
        if verify_plugin_signature(
            integrity_hash_hex, signature_hex, [key.public_key_hex]
        ):
            logger.error(
                "Refusing plugin %s: signature_ed25519 was produced by trust "
                "store key %s, which is scoped to other plugins.",
                safe_name,
                key.key_id,
            )
            return False
    if not usable:
        logger.error(
            "Refusing plugin %s: every configured trust root is revoked or "
            "expired (%d key(s)).",
            safe_name,
            len(rejected),
        )
        return False
    logger.error(
        "Refusing plugin %s: signature_ed25519 does not verify against "
        "any configured trust root.",
        safe_name,
    )
    return False


__all__ = [
    "TrustedKey",
    "enforce_plugin_signature",
    "generate_keypair_hex",
    "is_signature_required",
    "load_trust_roots",
    "load_trust_store",
    "load_trusted_keys",
    "sign_message",
    "sign_plugin_hash",
    "verify_message",
    "verify_plugin_signature",
]
