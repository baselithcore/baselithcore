"""
Per-tenant encryption-at-rest.

Derives a **tenant-bound** key from the operator's base key material so each
tenant's ciphertext is cryptographically isolated: data encrypted in tenant A's
context cannot be decrypted in tenant B's context, even with full database
access. The derivation is HKDF-SHA256 with the tenant id mixed into the ``info``
parameter, applied on top of the framework's existing
:class:`~core.security.encryption.FieldEncryptor` (AES-256-GCM, versioned keys).

This is the *primitive*; which fields a given store encrypts per tenant is a
store-level decision.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretStr

from core.security.encryption import FieldEncryptor, _derive_key

_KEY_BYTES = 32
_TENANT_INFO_PREFIX = b"baselithcore.tenant-encryption.v1:"


def derive_tenant_key_material(base_material: str, tenant_id: str) -> str:
    """Derive base64 key material bound to ``tenant_id`` from a base secret.

    The base secret is first normalized to a 32-byte key (the same way
    :class:`FieldEncryptor` treats raw keys/passphrases), then HKDF-expanded with
    the tenant id so the result is unique per tenant. The return value is raw
    base64 (32 bytes) suitable for :meth:`FieldEncryptor.from_keys`.
    """
    base_key = _derive_key(base_material)
    hkdf = HKDF(
        algorithm=SHA256(),
        length=_KEY_BYTES,
        salt=None,
        info=_TENANT_INFO_PREFIX + tenant_id.encode("utf-8"),
    )
    tenant_key = hkdf.derive(base_key)
    return base64.urlsafe_b64encode(tenant_key).decode("ascii")


def tenant_field_encryptor(
    tenant_id: str,
    base_keys: dict[str, str],
    active_key_id: str | None = None,
) -> FieldEncryptor:
    """Build a :class:`FieldEncryptor` whose keys are bound to ``tenant_id``.

    Every base key is replaced by its tenant-derived counterpart (same key ids,
    so versioning/rotation semantics are preserved per tenant).

    Args:
        tenant_id: The tenant whose data this encryptor protects.
        base_keys: Mapping of ``key_id -> base secret material``.
        active_key_id: Id of the key used for new encryptions.
    """
    scoped: dict[str, SecretStr | str] = {
        key_id: derive_tenant_key_material(material, tenant_id)
        for key_id, material in base_keys.items()
    }
    return FieldEncryptor.from_keys(scoped, active_key_id)
