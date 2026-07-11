"""Refresh-token rotation-family revocation (RFC 9700 §4.14.2).

Presenting an already-rotated (blacklisted) refresh token signals theft: the
handler must revoke the whole rotation lineage so the thief's freshly rotated
descendant stops working too.
"""

from unittest.mock import AsyncMock, patch

import jwt as pyjwt
import pytest

from core.auth.jwt import JWTHandler
from core.auth.types import AuthRole, InvalidTokenError

SECRET = "test-secret-with-at-least-thirty-two-chars"


class FakeRedis:
    """Minimal async Redis stand-in backed by a dict (get/setex only)."""

    def __init__(self):
        self.store: dict[str, bytes] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def handler(redis):
    with patch("core.auth.jwt.create_redis_client", return_value=redis):
        yield JWTHandler(secret_key=SECRET, algorithm="HS256")


def _claims(token: str) -> dict:
    return pyjwt.decode(token, SECRET, algorithms=["HS256"])


def test_fresh_refresh_token_starts_its_own_family(handler):
    token = handler.create_refresh_token("u1")
    claims = _claims(token)
    assert claims["family"] == claims["jti"]


@pytest.mark.asyncio
async def test_family_claim_survives_rotation(handler):
    """Every descendant carries the ORIGINAL family id."""
    first = handler.create_refresh_token("u1")
    family = _claims(first)["family"]

    _, second = await handler.rotate_refresh_token(first)
    claims2 = _claims(second)
    assert claims2["family"] == family
    assert claims2["jti"] != family  # new token, same lineage


def test_extra_claims_cannot_forge_family(handler):
    """`family` is a reserved claim — caller-supplied values are dropped."""
    token = handler.create_refresh_token("u1", extra_claims={"family": "evil"})
    assert _claims(token)["family"] != "evil"


@pytest.mark.asyncio
async def test_rotated_token_reuse_revokes_family(handler, redis):
    """Replaying a consumed refresh token kills the thief's descendant too."""
    stolen = handler.create_refresh_token("victim")

    # Thief rotates first and now holds a valid descendant.
    _, thief_refresh = await handler.rotate_refresh_token(stolen)
    assert await handler.verify_token(thief_refresh, expected_type="refresh")

    # Victim (or thief again) presents the consumed token: reuse detected.
    with pytest.raises(InvalidTokenError):
        await handler.rotate_refresh_token(stolen)

    # The whole family is now revoked — the descendant is dead as well.
    handler._verify_cache.clear()  # skip the short in-process verify cache
    with pytest.raises(InvalidTokenError, match="family"):
        await handler.verify_token(thief_refresh, expected_type="refresh")


@pytest.mark.asyncio
async def test_unrelated_family_unaffected(handler, redis):
    """Revoking one lineage never touches another user's tokens."""
    stolen = handler.create_refresh_token("victim")
    other = handler.create_refresh_token("bystander")

    await handler.rotate_refresh_token(stolen)
    with pytest.raises(InvalidTokenError):
        await handler.rotate_refresh_token(stolen)

    handler._verify_cache.clear()
    user = await handler.verify_token(other, expected_type="refresh")
    assert user.user_id == "bystander"


@pytest.mark.asyncio
async def test_access_tokens_skip_family_checks(handler, redis):
    """Access tokens carry no family logic — no extra Redis lookups."""
    spy = AsyncMock(wraps=redis.get)
    redis.get = spy
    token = handler.create_token("u1", roles={AuthRole.USER})
    await handler.verify_token(token)
    # Exactly one blacklist GET (jti), no family GET.
    assert spy.await_count == 1


@pytest.mark.asyncio
async def test_legacy_refresh_token_without_family_still_rotates(handler, redis):
    """Tokens minted before the family claim keep working (no family logic)."""
    import time

    now = int(time.time())
    legacy = pyjwt.encode(
        {
            "sub": "u1",
            "iat": now,
            "exp": now + 3600,
            "jti": "legacyjti",
            "type": "refresh",
        },
        SECRET,
        algorithm="HS256",
    )
    new_access, new_refresh = await handler.rotate_refresh_token(legacy)
    assert new_access and new_refresh
    # The new refresh starts a fresh family.
    claims = _claims(new_refresh)
    assert claims["family"] == claims["jti"]
