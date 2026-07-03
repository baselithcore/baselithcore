"""Tests for the in-process JWT verify TTL cache (hit / miss / expiry).

The cache lets repeated authenticated requests skip both the signature check
(``jwt.decode``) and the Redis blacklist round-trip for a short window, keyed on
a sha256 hash of the raw token.
"""

import hashlib
from unittest.mock import AsyncMock, patch

import jwt
import pytest

from core.auth.jwt import JWTHandler
from core.auth.types import AuthRole


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get.return_value = None
    return redis


@pytest.fixture
def handler(mock_redis):
    with patch("core.auth.jwt.create_redis_client", return_value=mock_redis):
        yield JWTHandler(
            secret_key="test-secret-with-at-least-thirty-two-chars",
            algorithm="HS256",
            token_lifetime=3600,
        )


@pytest.mark.asyncio
async def test_first_verify_is_a_cache_miss(handler, mock_redis):
    """A first verification decodes the token and hits Redis, then caches it."""
    token = handler.create_token("user123", roles={AuthRole.USER})

    user = await handler.verify_token(token)

    assert user.user_id == "user123"
    # Redis blacklist consulted exactly once on the miss path.
    assert mock_redis.get.await_count == 1
    # Entry stored under the sha256(token) key, never the raw token.
    cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert cache_key in handler._verify_cache
    assert token not in handler._verify_cache


@pytest.mark.asyncio
async def test_second_verify_is_a_cache_hit(handler, mock_redis):
    """A repeat verification skips both jwt.decode and the Redis lookup."""
    token = handler.create_token("user123", roles={AuthRole.USER})

    first = await handler.verify_token(token)
    assert mock_redis.get.await_count == 1

    with patch("core.auth.jwt.jwt.decode", side_effect=AssertionError) as decode_spy:
        second = await handler.verify_token(token)

    # decode must NOT be called on the hit path.
    decode_spy.assert_not_called()
    # Redis must NOT be consulted again on the hit path.
    assert mock_redis.get.await_count == 1
    assert second is first


@pytest.mark.asyncio
async def test_cache_expiry_forces_reverification(handler, mock_redis):
    """Once the TTL elapses, the next call re-decodes and re-checks Redis."""
    token = handler.create_token("user123", roles={AuthRole.USER})

    # t0: miss -> populates cache with expiry at t0 + ttl.
    with patch("core.auth.jwt.time.monotonic", return_value=1000.0):
        await handler.verify_token(token)
    assert mock_redis.get.await_count == 1

    # Still within the window: hit, no extra decode/redis.
    with patch("core.auth.jwt.time.monotonic", return_value=1003.0):
        with patch(
            "core.auth.jwt.jwt.decode", side_effect=AssertionError
        ) as decode_spy:
            await handler.verify_token(token)
    decode_spy.assert_not_called()
    assert mock_redis.get.await_count == 1

    # Past the max TTL window: entry is stale -> full re-verification.
    with patch("core.auth.jwt.time.monotonic", return_value=2000.0):
        user = await handler.verify_token(token)
    assert user.user_id == "user123"
    assert mock_redis.get.await_count == 2


@pytest.mark.asyncio
async def test_revoke_evicts_local_cache_entry(handler, mock_redis):
    """Revoking a token drops its cached verification immediately in-process."""
    token = handler.create_token("user123", roles={AuthRole.USER})

    await handler.verify_token(token)
    cache_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert cache_key in handler._verify_cache

    await handler.revoke_token(token)
    assert cache_key not in handler._verify_cache

    # After revocation, the next verify is a miss that consults Redis again;
    # simulate the blacklist now containing the jti.
    mock_redis.get.return_value = b"1"
    from core.auth.types import InvalidTokenError

    with pytest.raises(InvalidTokenError):
        await handler.verify_token(token)


@pytest.mark.asyncio
async def test_expired_token_is_not_cached(handler):
    """A token that fails verification (expired) must not populate the cache."""
    # Build an already-expired token directly.
    import time as _time

    payload = {
        "sub": "user123",
        "iat": int(_time.time()) - 10,
        "exp": int(_time.time()) - 1,
        "jti": "deadbeef",
        "roles": ["user"],
    }
    token = jwt.encode(
        payload, "test-secret-with-at-least-thirty-two-chars", algorithm="HS256"
    )

    from core.auth.types import TokenExpiredError

    with pytest.raises(TokenExpiredError):
        await handler.verify_token(token)

    assert handler._verify_cache == {}


@pytest.mark.asyncio
async def test_cache_is_bounded_by_max_entries(handler):
    """A flood of distinct valid tokens must not grow the cache unbounded."""
    from core.auth import jwt as jwt_mod

    # Shrink the cap so the test stays fast; restore afterwards.
    original_cap = jwt_mod._VERIFY_CACHE_MAX_ENTRIES
    jwt_mod._VERIFY_CACHE_MAX_ENTRIES = 4
    try:
        tokens = [
            handler.create_token(f"user{i}", roles={AuthRole.USER}) for i in range(20)
        ]
        for tok in tokens:
            await handler.verify_token(tok)

        # Never exceeds the cap despite 20 distinct verifications.
        assert len(handler._verify_cache) <= 4

        # LRU semantics: the most recently verified token is retained, the
        # earliest ones are evicted.
        newest_key = hashlib.sha256(tokens[-1].encode("utf-8")).hexdigest()
        oldest_key = hashlib.sha256(tokens[0].encode("utf-8")).hexdigest()
        assert newest_key in handler._verify_cache
        assert oldest_key not in handler._verify_cache
    finally:
        jwt_mod._VERIFY_CACHE_MAX_ENTRIES = original_cap
