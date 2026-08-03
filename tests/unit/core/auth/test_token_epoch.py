"""Invalidating access tokens you do not hold.

Blacklisting a `jti` needs the token. The cases that matter most are the ones
where you do not have it — disabling an account, a password change after a
compromise, "sign out everywhere". Those revoke the refresh token, but every
access token already minted keeps working until it expires. An epoch closes
that window: bump the user's counter and their whole token population stops
verifying at once.
"""

from unittest.mock import patch

import jwt as pyjwt
import pytest

from core.auth._token_epoch import NO_EPOCH
from core.auth.types import InvalidTokenError

SECRET = "epoch-test-secret-with-at-least-thirty-two-chars"


class _FakeRedis:
    """Just enough Redis: a counter per key, and get/incr over it."""

    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.fail = False

    async def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def incr(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        value = int(self.store.get(key, b"0")) + 1
        self.store[key] = str(value).encode()
        return value

    async def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.fixture
def handler():
    from core.auth.jwt import JWTHandler

    redis = _FakeRedis()
    with patch("core.auth.jwt.create_redis_client", return_value=redis):
        h = JWTHandler(secret_key=SECRET)
    h._fake_redis = redis  # exposed so tests can simulate an outage
    return h


async def _mint(handler, user_id="u-1"):
    """Mint a token stamped with the user's current epoch, as AuthManager does."""
    return handler.create_token(
        user_id, token_epoch=await handler.current_user_epoch(user_id)
    )


# --------------------------------------------------------------------------- #
# The core behaviour
# --------------------------------------------------------------------------- #


async def test_token_verifies_before_any_bump(handler):
    user = await handler.verify_token(await _mint(handler))
    assert user.user_id == "u-1"


async def test_bump_strands_tokens_already_issued(handler):
    """The point of the feature: no jti needed, no enumeration."""
    token = await _mint(handler)
    await handler.bump_user_epoch("u-1")
    with pytest.raises(InvalidTokenError, match="invalidated"):
        await handler.verify_token(token)


async def test_tokens_minted_after_the_bump_work(handler):
    await handler.bump_user_epoch("u-1")
    user = await handler.verify_token(await _mint(handler))
    assert user.user_id == "u-1"


async def test_bump_is_scoped_to_one_user(handler):
    other = await _mint(handler, "u-2")
    await handler.bump_user_epoch("u-1")
    assert (await handler.verify_token(other)).user_id == "u-2"


async def test_repeated_bumps_keep_climbing(handler):
    assert await handler.bump_user_epoch("u-1") == 1
    assert await handler.bump_user_epoch("u-1") == 2
    assert await handler.current_user_epoch("u-1") == 2


# --------------------------------------------------------------------------- #
# Compatibility and failure modes
# --------------------------------------------------------------------------- #


async def test_token_without_an_epoch_claim_is_accepted(handler):
    """Rejecting these would sign out every active user on deploy."""
    token = handler.create_token("u-1")
    assert "tv" not in pyjwt.decode(token, options={"verify_signature": False})
    assert (await handler.verify_token(token)).user_id == "u-1"


async def test_epoch_higher_than_the_store_is_rejected(handler):
    """A wiped store must not resurrect the sessions a bump ended."""
    await handler.bump_user_epoch("u-1")
    token = await _mint(handler)
    handler._fake_redis.store.clear()
    with pytest.raises(InvalidTokenError, match="invalidated"):
        await handler.verify_token(token)


async def test_epoch_cannot_be_forged_through_extra_claims(handler):
    """`tv` is reserved, or a caller could opt their token out of invalidation."""
    await handler.bump_user_epoch("u-1")
    token = handler.create_token("u-1", extra_claims={"tv": 99})
    assert "tv" not in pyjwt.decode(token, options={"verify_signature": False})


async def test_minting_survives_an_epoch_store_outage(handler):
    """Better an unprotected token than no tokens at all."""
    handler._fake_redis.fail = True
    assert await handler.current_user_epoch("u-1") == NO_EPOCH


async def test_a_failed_bump_reports_failure(handler):
    """The caller must not tell a user they were signed out when they weren't."""
    handler._fake_redis.fail = True
    assert await handler.bump_user_epoch("u-1") == NO_EPOCH


# --------------------------------------------------------------------------- #
# The AuthManager seam plugins actually call
# --------------------------------------------------------------------------- #


async def test_manager_reports_whether_the_invalidation_landed():
    from core.auth.manager import AuthManager

    redis = _FakeRedis()
    with patch("core.auth.jwt.create_redis_client", return_value=redis):
        manager = AuthManager(secret_key=SECRET)

    assert await manager.revoke_user_tokens("u-1") is True
    redis.fail = True
    assert await manager.revoke_user_tokens("u-1") is False


async def test_manager_stamps_the_current_epoch_on_new_tokens():
    from core.auth.manager import AuthManager

    redis = _FakeRedis()
    with patch("core.auth.jwt.create_redis_client", return_value=redis):
        manager = AuthManager(secret_key=SECRET)

    await manager.revoke_user_tokens("u-1")
    token = await manager.create_token("u-1")
    assert pyjwt.decode(token, options={"verify_signature": False})["tv"] == 1


async def test_manager_revocation_ends_existing_tokens():
    from core.auth.manager import AuthManager

    redis = _FakeRedis()
    with patch("core.auth.jwt.create_redis_client", return_value=redis):
        manager = AuthManager(secret_key=SECRET)

    token = await manager.create_token("u-1")
    assert (await manager.jwt.verify_token(token)).user_id == "u-1"

    await manager.revoke_user_tokens("u-1")
    # The handler caches successful verifications for a few seconds; clear it so
    # the test measures the epoch check rather than the cache.
    manager.jwt._verify_cache.clear()
    with pytest.raises(InvalidTokenError):
        await manager.jwt.verify_token(token)
