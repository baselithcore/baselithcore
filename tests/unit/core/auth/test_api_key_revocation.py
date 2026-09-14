"""Distributed API-key revocation: Redis-backed denylist shared across workers."""

import hashlib
from unittest.mock import AsyncMock

from core.auth.api_keys import APIKeyValidator
from core.auth.types import AuthRole
from core.config.security import SecurityConfig


def _validator_with_redis(redis) -> APIKeyValidator:
    validator = APIKeyValidator(config=SecurityConfig())
    validator._redis = redis
    validator.register_key("k-123", "svc", {AuthRole.SERVICE})
    return validator


async def test_revoke_writes_shared_denylist():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    validator = _validator_with_redis(redis)

    assert await validator.revoke_key("k-123") is True
    hashed = hashlib.sha256(b"k-123").hexdigest()
    redis.set.assert_awaited_once()
    assert hashed in redis.set.await_args.args[0]
    assert await validator.validate_key("k-123") is None


async def test_validate_rejects_key_revoked_by_another_worker():
    redis = AsyncMock()
    # Key still present in this process (revoked elsewhere): denylist hit.
    redis.exists = AsyncMock(return_value=1)
    validator = _validator_with_redis(redis)

    assert await validator.validate_key("k-123") is None


async def test_validate_survives_redis_outage_when_opened():
    """Fail-open is now an explicit opt-in (API_KEY_REVOCATION_FAIL_MODE).

    The default is ``closed`` — see
    ``tests/unit/core/auth/test_api_key_revocation_fail_mode.py`` — because an
    unreadable denylist otherwise un-revokes every key revoked elsewhere.
    """
    redis = AsyncMock()
    redis.exists = AsyncMock(side_effect=ConnectionError("redis down"))
    validator = APIKeyValidator(
        config=SecurityConfig(API_KEY_REVOCATION_FAIL_MODE="open")
    )
    validator._redis = redis
    validator.register_key("k-123", "svc", {AuthRole.SERVICE})

    user = await validator.validate_key("k-123")
    assert user is not None
    assert user.user_id == "svc"


async def test_no_redis_falls_back_to_local_only():
    validator = APIKeyValidator(config=SecurityConfig())
    validator._redis = None
    validator.register_key("k-456", "svc2", {AuthRole.SERVICE})
    assert (await validator.validate_key("k-456")).user_id == "svc2"
    assert await validator.revoke_key("k-456") is True
    assert await validator.validate_key("k-456") is None
