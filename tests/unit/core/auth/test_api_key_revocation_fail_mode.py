"""Denylist fail mode: a Redis outage must not un-revoke an API key.

The shared denylist is the only thing that makes ``revoke_key`` reach other
workers and survive a restart. Reading it used to fail *open* — so an attacker
who could make Redis unreachable (or an outage that simply happened) restored
every revoked key to full access. The behaviour is now an explicit setting that
defaults to ``closed``.
"""

from unittest.mock import AsyncMock

import pytest

from core.auth.api_keys import APIKeyValidator
from core.auth.types import AuthRole
from core.config.security import SecurityConfig


def _validator(redis, **config_overrides) -> APIKeyValidator:
    validator = APIKeyValidator(config=SecurityConfig(**config_overrides))
    validator._redis = redis
    validator.register_key("k-123", "svc", {AuthRole.SERVICE})
    return validator


def _down_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.exists = AsyncMock(side_effect=ConnectionError("redis down"))
    return redis


def test_fail_mode_defaults_to_closed():
    assert SecurityConfig().api_key_revocation_fail_mode == "closed"


def test_fail_mode_rejects_unknown_values():
    with pytest.raises(ValueError):
        SecurityConfig(API_KEY_REVOCATION_FAIL_MODE="maybe")


async def test_redis_outage_denies_the_key_by_default():
    validator = _validator(_down_redis())

    assert await validator.validate_key("k-123") is None


async def test_redis_outage_denies_the_key_when_closed():
    validator = _validator(_down_redis(), API_KEY_REVOCATION_FAIL_MODE="closed")

    assert await validator.validate_key("k-123") is None


async def test_redis_outage_keeps_serving_when_explicitly_opened():
    validator = _validator(_down_redis(), API_KEY_REVOCATION_FAIL_MODE="open")

    user = await validator.validate_key("k-123")
    assert user is not None
    assert user.user_id == "svc"


async def test_closed_mode_logs_the_denial(monkeypatch):
    recorded: list[tuple[str, dict]] = []

    validator = _validator(_down_redis())
    monkeypatch.setattr(
        "core.auth.api_keys.logger",
        type(
            "_Logger",
            (),
            {
                "error": staticmethod(lambda msg, **kw: recorded.append((msg, kw))),
                "warning": staticmethod(lambda msg, **kw: recorded.append((msg, kw))),
            },
        )(),
    )

    assert await validator.validate_key("k-123") is None
    assert any("denylist" in msg for msg, _ in recorded)


async def test_healthy_redis_is_unaffected_by_the_fail_mode():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    validator = _validator(redis)

    assert (await validator.validate_key("k-123")).user_id == "svc"


async def test_no_redis_configured_still_serves_local_keys():
    """No denylist backend at all is a deployment choice, not an outage."""
    validator = _validator(None)
    validator._denylist_declared = False  # CACHE_BACKEND != redis

    assert (await validator.validate_key("k-123")).user_id == "svc"


# ---------------------------------------------------------------------------
# Backend selection: the denylist exists only when Redis is *declared*
# ---------------------------------------------------------------------------


def _storage(backend: str):
    from types import SimpleNamespace

    return SimpleNamespace(cache_backend=backend)


async def test_no_declared_redis_uses_process_local_revocation(monkeypatch):
    """CACHE_BACKEND != redis: keys work although no Redis is reachable.

    ``create_redis_client`` is lazy, so building a client never failed and the
    first denylist read against an unreachable server rejected *every* key.
    """
    import core.config as config_mod

    monkeypatch.setattr(config_mod, "get_storage_config", lambda: _storage("local"))
    created = []
    monkeypatch.setattr(
        "core.cache.redis_cache.create_redis_client",
        lambda url: created.append(url) or _down_redis(),
    )
    validator = APIKeyValidator(config=SecurityConfig())
    validator.register_key("k-123", "svc", {AuthRole.SERVICE})

    assert created == []
    assert validator._redis is None
    assert await validator.validate_key("k-123") is not None
    # Revocation still takes effect in this process.
    assert await validator.revoke_key("k-123") is True
    assert await validator.validate_key("k-123") is None


async def test_declared_redis_down_still_fails_closed(monkeypatch):
    import core.config as config_mod

    monkeypatch.setattr(config_mod, "get_storage_config", lambda: _storage("redis"))
    monkeypatch.setattr(
        "core.cache.redis_cache.create_redis_client", lambda url: _down_redis()
    )
    validator = APIKeyValidator(config=SecurityConfig())
    validator.register_key("k-123", "svc", {AuthRole.SERVICE})

    assert validator._redis is not None
    assert await validator.validate_key("k-123") is None


async def test_declared_redis_client_build_failure_fails_closed(monkeypatch):
    import core.config as config_mod

    def _boom(url):
        raise ValueError("bad redis url")

    monkeypatch.setattr(config_mod, "get_storage_config", lambda: _storage("redis"))
    monkeypatch.setattr("core.cache.redis_cache.create_redis_client", _boom)
    validator = APIKeyValidator(config=SecurityConfig())
    validator.register_key("k-123", "svc", {AuthRole.SERVICE})

    assert validator._redis is None
    assert await validator.validate_key("k-123") is None
    opened = APIKeyValidator(config=SecurityConfig(API_KEY_REVOCATION_FAIL_MODE="open"))
    opened.register_key("k-123", "svc", {AuthRole.SERVICE})
    assert await opened.validate_key("k-123") is not None
