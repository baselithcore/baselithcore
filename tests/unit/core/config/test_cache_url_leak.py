"""The cache Redis URL must never leak its password through serialization.

``StorageConfig`` redacts ``user:password@`` from all its DSN fields on dump
and repr, but the separately-loaded ``RedisCacheConfig`` (same
``CACHE_REDIS_URL``, used by the JWT blacklist, the rate limiter and the
API-key denylist) used to print an embedded Redis password verbatim in any
config dump or Sentry frame.
"""

from __future__ import annotations

from core.config.cache import RedisCacheConfig

_URL = "redis://:sup3r-s3cret-pw@cache.internal:6379/1"


def _config() -> RedisCacheConfig:
    return RedisCacheConfig(CACHE_REDIS_URL=_URL)


def test_attribute_access_keeps_usable_url() -> None:
    assert _config().url == _URL  # callers still connect with the real value


def test_repr_redacts_password() -> None:
    assert "sup3r-s3cret-pw" not in repr(_config())


def test_model_dump_redacts_password() -> None:
    cfg = _config()
    assert "sup3r-s3cret-pw" not in str(cfg.model_dump())
    assert "sup3r-s3cret-pw" not in cfg.model_dump_json()
