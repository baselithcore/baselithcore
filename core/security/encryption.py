"""Application-level encryption-at-rest primitives.

Provides authenticated, versioned field encryption built on AES-256-GCM. The
encryptor is intentionally domain-agnostic: it turns a ``str`` into an opaque,
self-describing token and back, so callers (storage layers, PII columns,
cached payloads) can protect data at rest without coupling to a specific
database or ORM.

Design goals
------------
- **Authenticated encryption.** AES-GCM provides confidentiality *and*
  integrity (tampered ciphertext fails to decrypt).
- **Key versioning + rotation.** Each token embeds the id of the key that
  produced it. Multiple keys can be loaded at once: encryption always uses the
  *active* key while decryption transparently selects the embedded key id. To
  rotate, add a new key, mark it active, and re-encrypt lazily (decrypt then
  encrypt) — old ciphertext keeps decrypting until fully migrated.
- **Key derivation.** Operators may supply either raw 32-byte keys (base64) or
  arbitrary passphrases; passphrases are stretched to 32 bytes with HKDF-SHA256
  so a human-typed secret still yields a uniform AES key.

Token format (URL-safe base64, ``:`` separated)::

    enc:v1:<key_id>:<b64(nonce(12) || ciphertext || tag)>

The ``enc:`` prefix lets :func:`FieldEncryptor.is_encrypted` cheaply detect
already-encrypted values and makes encryption idempotent-safe at call sites.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretStr

logger = logging.getLogger(__name__)

#: Token scheme prefix and version. Bump ``_VERSION`` only on format changes.
_PREFIX = "enc"
_VERSION = "v1"
#: AES-GCM standard nonce size (96 bits) and key size (256 bits).
_NONCE_BYTES = 12
_KEY_BYTES = 32
#: HKDF context label — binds derived keys to this application/purpose.
_HKDF_INFO = b"baselithcore.encryption-at-rest.v1"


class EncryptionError(Exception):
    """Base class for encryption/decryption failures."""


class DecryptionError(EncryptionError):
    """Raised when a token cannot be authenticated or decrypted.

    Covers tampered ciphertext, an unknown key id, and malformed tokens. The
    message never includes plaintext or key material.
    """


def _derive_key(material: str) -> bytes:
    """Derive a 32-byte AES key from operator-supplied secret material.

    Raw 32-byte keys provided as standard or URL-safe base64 are used directly;
    any other string is treated as a passphrase and stretched via HKDF-SHA256.

    Args:
        material: Base64-encoded 32-byte key, or an arbitrary passphrase.

    Returns:
        A 32-byte key suitable for AES-256-GCM.
    """
    raw = material.strip()
    # Fast path: caller already provided exactly 32 raw bytes as base64.
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            candidate = decoder(_pad_b64(raw))
        except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
            continue
        if len(candidate) == _KEY_BYTES:
            return candidate
    # Otherwise treat the secret as a passphrase and stretch it.
    hkdf = HKDF(algorithm=SHA256(), length=_KEY_BYTES, salt=None, info=_HKDF_INFO)
    return hkdf.derive(raw.encode("utf-8"))


def _pad_b64(value: str) -> str:
    """Restore missing ``=`` padding so base64 decoders accept the input."""
    return value + "=" * (-len(value) % 4)


@dataclass(frozen=True)
class _Key:
    """An immutable, in-memory AES key bound to its id."""

    key_id: str
    aesgcm: AESGCM


class FieldEncryptor:
    """Versioned AES-256-GCM encryptor for at-rest field protection.

    Construct via :meth:`from_keys` (operator-facing secret strings) rather than
    directly. The encryptor is stateless beyond its loaded keys and therefore
    safe to share across threads and async tasks.
    """

    def __init__(self, keys: dict[str, _Key], active_key_id: str) -> None:
        if not keys:
            raise EncryptionError("FieldEncryptor requires at least one key.")
        if active_key_id not in keys:
            raise EncryptionError(
                f"Active key id {active_key_id!r} is not among loaded keys."
            )
        self._keys = keys
        self._active_key_id = active_key_id

    @classmethod
    def from_keys(
        cls,
        keys: dict[str, SecretStr | str],
        active_key_id: str | None = None,
    ) -> FieldEncryptor:
        """Build an encryptor from a mapping of ``key_id -> secret``.

        Args:
            keys: Mapping of opaque key ids to secret material (raw base64 key
                or passphrase). Key ids must not contain ``:``.
            active_key_id: Id of the key used for *new* encryptions. Defaults to
                the single provided key, or fails if ambiguous.

        Returns:
            A configured :class:`FieldEncryptor`.

        Raises:
            EncryptionError: If no keys are supplied, an id contains ``:``, or
                ``active_key_id`` is omitted with multiple keys.
        """
        if not keys:
            raise EncryptionError("At least one encryption key must be provided.")
        built: dict[str, _Key] = {}
        for key_id, secret in keys.items():
            if ":" in key_id:
                raise EncryptionError(f"Key id {key_id!r} must not contain ':'.")
            raw = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
            built[key_id] = _Key(key_id=key_id, aesgcm=AESGCM(_derive_key(raw)))
        if active_key_id is None:
            if len(built) != 1:
                raise EncryptionError(
                    "active_key_id is required when multiple keys are loaded."
                )
            active_key_id = next(iter(built))
        return cls(built, active_key_id)

    @property
    def active_key_id(self) -> str:
        """Id of the key used for new encryptions."""
        return self._active_key_id

    @staticmethod
    def is_encrypted(value: str) -> bool:
        """Return ``True`` if ``value`` looks like an encryption token.

        Used to keep encryption idempotent (skip already-encrypted values) and
        decryption tolerant (pass through plaintext during migration). Matches
        any version of the scheme, so a token from an unsupported version is
        still recognised as a token and surfaces as a :class:`DecryptionError`
        rather than being silently treated as plaintext.
        """
        return isinstance(value, str) and value.startswith(f"{_PREFIX}:")

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string, returning a self-describing token.

        Already-encrypted inputs are returned unchanged so the call is safe to
        apply more than once.

        Args:
            plaintext: The value to protect.

        Returns:
            An ``enc:v1:<key_id>:<b64>`` token.
        """
        if self.is_encrypted(plaintext):
            return plaintext
        return self.encrypt_bytes(plaintext.encode("utf-8"))

    def encrypt_bytes(self, data: bytes, *, aad: bytes | None = None) -> str:
        """Encrypt raw bytes with optional associated data (AAD).

        Args:
            data: Plaintext bytes.
            aad: Optional additional authenticated data bound to the ciphertext
                (e.g. a tenant id or column name). The same AAD must be supplied
                to :meth:`decrypt_bytes`.

        Returns:
            An ``enc:v1:<key_id>:<b64>`` token.
        """
        key = self._keys[self._active_key_id]
        nonce = self._random_nonce()
        ciphertext = key.aesgcm.encrypt(nonce, data, aad)
        blob = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return f"{_PREFIX}:{_VERSION}:{key.key_id}:{blob}"

    def decrypt(self, token: str, *, aad: bytes | None = None) -> str:
        """Decrypt a token back to its original string.

        Values that are not tokens are returned unchanged, easing rollout over
        existing plaintext data.

        Args:
            token: An ``enc:v1`` token, or a plaintext passthrough value.
            aad: Associated data supplied at encryption time, if any.

        Returns:
            The decrypted UTF-8 string (or the input if not a token).

        Raises:
            DecryptionError: On tampering, unknown key id, or malformed token.
        """
        if not self.is_encrypted(token):
            return token
        return self.decrypt_bytes(token, aad=aad).decode("utf-8")

    def decrypt_bytes(self, token: str, *, aad: bytes | None = None) -> bytes:
        """Decrypt a token to raw bytes.

        Raises:
            DecryptionError: On tampering, unknown key id, or malformed token.
        """
        try:
            prefix, version, key_id, blob = token.split(":", 3)
        except ValueError as exc:
            raise DecryptionError("Malformed encryption token.") from exc
        if (prefix, version) != (_PREFIX, _VERSION):
            raise DecryptionError(f"Unsupported token scheme {prefix}:{version}.")
        key = self._keys.get(key_id)
        if key is None:
            raise DecryptionError(f"Unknown encryption key id {key_id!r}.")
        try:
            raw = base64.urlsafe_b64decode(_pad_b64(blob))
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise DecryptionError("Token payload is not valid base64.") from exc
        if len(raw) <= _NONCE_BYTES:
            raise DecryptionError("Token payload is truncated.")
        nonce, ciphertext = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        try:
            return key.aesgcm.decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise DecryptionError(
                "Authentication failed: ciphertext was tampered with or the "
                "wrong key/AAD was used."
            ) from exc

    def needs_rotation(self, token: str) -> bool:
        """Return ``True`` if ``token`` was produced by a non-active key.

        Lets a background job find ciphertext eligible for re-encryption under
        the current active key.
        """
        if not self.is_encrypted(token):
            return False
        try:
            _, _, key_id, _ = token.split(":", 3)
        except ValueError:
            return False
        return key_id != self._active_key_id

    @staticmethod
    def _random_nonce() -> bytes:
        """Return a fresh 96-bit nonce from the OS CSPRNG."""
        # Imported lazily and locally to keep the module import-light and to
        # avoid any module-level RNG state.
        import os

        return os.urandom(_NONCE_BYTES)


__all__ = [
    "DecryptionError",
    "EncryptionError",
    "FieldEncryptor",
]
