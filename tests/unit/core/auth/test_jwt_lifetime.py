"""Tests for the per-token access lifetime override (H1/H2 regression guard).

``exp`` is a reserved claim stripped from ``extra_claims``, so a bounded TTL
(impersonation, short sessions) must travel through the first-class ``lifetime``
parameter added to ``JWTHandler.create_token`` / ``AuthManager.create_token``.
These tests pin that the override is honoured and that config drives the default
so ``AUTH_SESSION_LIFETIME`` actually shortens the issued token.
"""

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


def _decode(handler, token):
    return jwt.decode(
        token,
        "test-secret-with-at-least-thirty-two-chars",
        algorithms=["HS256"],
    )


def test_default_lifetime_used_when_override_omitted(handler):
    token = handler.create_token("u1", roles={AuthRole.USER})
    claims = _decode(handler, token)
    assert claims["exp"] - claims["iat"] == 3600


def test_lifetime_override_shortens_token(handler):
    token = handler.create_token("u1", roles={AuthRole.USER}, lifetime=120)
    claims = _decode(handler, token)
    assert claims["exp"] - claims["iat"] == 120


def test_lifetime_override_is_clamped_to_at_least_one_second(handler):
    token = handler.create_token("u1", roles={AuthRole.USER}, lifetime=0)
    claims = _decode(handler, token)
    assert claims["exp"] - claims["iat"] == 1


def test_exp_in_extra_claims_is_ignored_but_lifetime_wins(handler):
    # exp is a reserved claim: passing it via extra_claims must NOT change exp;
    # the lifetime parameter is the only channel that does.
    token = handler.create_token(
        "u1",
        roles={AuthRole.USER},
        extra_claims={"exp": 10_000_000, "custom": "x"},
        lifetime=300,
    )
    claims = _decode(handler, token)
    assert claims["exp"] - claims["iat"] == 300
    assert claims["custom"] == "x"


@pytest.mark.asyncio
async def test_auth_manager_passes_lifetime_through(mock_redis):
    from core.auth.manager import AuthManager
    from core.config.security import SecurityConfig

    cfg = SecurityConfig(
        SECRET_KEY="test-secret-with-at-least-thirty-two-chars",
        AUTH_ACCESS_TOKEN_LIFETIME=900,
    )
    with patch("core.auth.jwt.create_redis_client", return_value=mock_redis):
        mgr = AuthManager(config=cfg)
        # Default comes from config (900), not the old hard-coded 3600.
        default_token = await mgr.create_token("u1", {AuthRole.USER})
        # Explicit per-token override wins.
        short_token = await mgr.create_token("u1", {AuthRole.USER}, lifetime=60)

    d1 = _decode(None, default_token)
    d2 = _decode(None, short_token)
    assert d1["exp"] - d1["iat"] == 900
    assert d2["exp"] - d2["iat"] == 60
