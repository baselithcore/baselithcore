"""The JWT key ring must never leak through model serialization.

With HS256 (the default algorithm) every ``JWT_KEYS`` entry is a
signing-capable shared secret, yet the field used to be a plain ``str`` — a
``repr(SecurityConfig)`` in a traceback or a config dump in a Sentry frame
printed the whole ring. It is now a ``SecretStr`` like ``secret_key`` /
``jwt_signing_key`` / ``admin_pass``.
"""

from __future__ import annotations

from pydantic import SecretStr

from core.auth._jwt_keys import parse_key_map
from core.config.security import SecurityConfig

_RING = "k1=hmac-secret-alpha,k2=hmac-secret-bravo"


def _config() -> SecurityConfig:
    return SecurityConfig(
        SECRET_KEY="x" * 40,
        JWT_KEYS=_RING,
        JWT_ACTIVE_KID="k1",
    )


def test_jwt_keys_is_secretstr() -> None:
    cfg = _config()
    assert isinstance(cfg.jwt_keys, SecretStr)
    assert cfg.jwt_keys.get_secret_value() == _RING


def test_jwt_keys_never_in_repr_or_dumps() -> None:
    cfg = _config()
    assert "hmac-secret-alpha" not in repr(cfg)
    assert "hmac-secret-alpha" not in str(cfg.model_dump())
    assert "hmac-secret-alpha" not in cfg.model_dump_json()


def test_parse_key_map_accepts_secretstr() -> None:
    """The consumer chain (AuthManager -> parse_key_map -> JWTKeyRing) must
    keep working when the config hands over a SecretStr."""
    assert parse_key_map(SecretStr(_RING)) == {
        "k1": "hmac-secret-alpha",
        "k2": "hmac-secret-bravo",
    }
