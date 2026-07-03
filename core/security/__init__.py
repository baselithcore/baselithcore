"""Domain-agnostic security infrastructure.

Bundles encryption-at-rest primitives (:mod:`core.security.encryption`) and
pluggable secret resolution (:mod:`core.security.secrets`). Both are opt-in:
with no encryption keys configured, :func:`get_field_encryptor` returns
``None`` and the framework behaves exactly as before.
"""

from __future__ import annotations

import logging

from core.security.encryption import (
    DecryptionError,
    EncryptionError,
    FieldEncryptor,
)
from core.security.secrets import (
    EnvSecretsProvider,
    FileSecretsProvider,
    SecretsProvider,
    get_secret,
    get_secrets_provider,
    register_secrets_provider,
    reset_secrets_provider,
)

logger = logging.getLogger(__name__)

#: Cached encryptor, built lazily from configuration on first use.
_field_encryptor: FieldEncryptor | None = None
_encryptor_loaded: bool = False


def get_field_encryptor() -> FieldEncryptor | None:
    """Return the process-wide field encryptor, or ``None`` if not configured.

    Builds a :class:`FieldEncryptor` from ``DATA_ENCRYPTION_KEYS`` /
    ``DATA_ENCRYPTION_ACTIVE_KEY_ID`` (see :class:`core.config.SecurityConfig`)
    on first call and caches the result. Returns ``None`` when no keys are set,
    so callers can degrade gracefully::

        enc = get_field_encryptor()
        stored = enc.encrypt(value) if enc else value

    The config import is deferred to avoid a circular import at module load.
    """
    global _field_encryptor, _encryptor_loaded
    if _encryptor_loaded:
        return _field_encryptor

    from core.config import get_security_config

    cfg = get_security_config()
    if cfg.data_encryption_keys:
        _field_encryptor = FieldEncryptor.from_keys(
            dict(cfg.data_encryption_keys),
            active_key_id=cfg.data_encryption_active_key_id,
        )
        logger.info(
            "Field encryption enabled (active_key_id=%s, %d key(s) loaded)",
            _field_encryptor.active_key_id,
            len(cfg.data_encryption_keys),
        )
    else:
        logger.debug("Field encryption not configured; encryptor is None.")
    _encryptor_loaded = True
    return _field_encryptor


def reset_field_encryptor() -> None:
    """Clear the cached encryptor. Intended for tests only."""
    global _field_encryptor, _encryptor_loaded
    _field_encryptor = None
    _encryptor_loaded = False


__all__ = [
    "DecryptionError",
    "EncryptionError",
    "EnvSecretsProvider",
    "FieldEncryptor",
    "FileSecretsProvider",
    "SecretsProvider",
    "get_field_encryptor",
    "get_secret",
    "get_secrets_provider",
    "register_secrets_provider",
    "reset_field_encryptor",
    "reset_secrets_provider",
]
