"""Tests for federated SSO / OIDC bearer-token verification.

A real RSA keypair signs RS256 tokens; the verifier's signing-key resolution is
patched to return the public key so no network/JWKS round-trip happens.
"""

import time
from unittest.mock import AsyncMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from core.auth.oidc import OIDCVerifier
from core.auth.types import AuthRole, InvalidTokenError
from core.config.security import SecurityConfig

ISSUER = "https://idp.example.com"
AUDIENCE = "baselith-api"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc_config():
    return SecurityConfig(
        SECRET_KEY="x" * 40,
        OIDC_ENABLED=True,
        OIDC_ISSUER=ISSUER,
        OIDC_AUDIENCE=AUDIENCE,
        OIDC_JWKS_URL=f"{ISSUER}/jwks",  # explicit → no discovery call
        OIDC_ROLES_CLAIM="groups",
        OIDC_ROLE_MAP="grp-admin:admin,grp-user:user",
        OIDC_TENANT_CLAIM="tenant",
    )


def _make_token(rsa_key, **overrides):
    now = int(time.time())
    payload = {
        "sub": "okta|123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 3600,
        "email": "user@example.com",
        "groups": ["grp-user"],
        "scope": "chat:read chat:write",
        "tenant": "acme",
    }
    payload.update(overrides)
    return pyjwt.encode(payload, rsa_key, algorithm="RS256")


@pytest.fixture
def verifier(oidc_config, rsa_key):
    """Verifier whose signing-key resolution returns the test public key."""
    v = OIDCVerifier(config=oidc_config)
    pub = rsa_key.public_key()
    with patch.object(OIDCVerifier, "_resolve_signing_key", return_value=pub):
        yield v


class TestConfiguration:
    def test_disabled_is_not_configured(self):
        v = OIDCVerifier(config=SecurityConfig(SECRET_KEY="x" * 40))
        assert not v.is_configured

    def test_enabled_is_configured(self, oidc_config):
        assert OIDCVerifier(config=oidc_config).is_configured

    @pytest.mark.asyncio
    async def test_verify_raises_when_unconfigured(self, rsa_key):
        v = OIDCVerifier(config=SecurityConfig(SECRET_KEY="x" * 40))
        with pytest.raises(InvalidTokenError, match="not configured"):
            await v.verify(_make_token(rsa_key))


class TestVerification:
    @pytest.mark.asyncio
    async def test_valid_token_maps_claims(self, verifier, rsa_key):
        user = await verifier.verify(_make_token(rsa_key))
        assert user.user_id == "okta|123"
        assert user.email == "user@example.com"
        assert user.tenant_id == "acme"
        assert AuthRole.USER in user.roles
        assert user.has_scope("chat:write")

    @pytest.mark.asyncio
    async def test_role_mapping_admin(self, verifier, rsa_key):
        user = await verifier.verify(_make_token(rsa_key, groups=["grp-admin"]))
        assert AuthRole.ADMIN in user.roles
        # Admin → superuser scope.
        assert user.has_scope("tenants:manage")

    @pytest.mark.asyncio
    async def test_unmapped_group_falls_back_to_default_role(self, verifier, rsa_key):
        user = await verifier.verify(_make_token(rsa_key, groups=["unknown-grp"]))
        assert AuthRole.USER in user.roles  # OIDC_DEFAULT_ROLE default

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, verifier, rsa_key):
        past = int(time.time()) - 10
        with pytest.raises(InvalidTokenError, match="expired"):
            await verifier.verify(_make_token(rsa_key, exp=past))

    @pytest.mark.asyncio
    async def test_wrong_audience_rejected(self, verifier, rsa_key):
        with pytest.raises(InvalidTokenError):
            await verifier.verify(_make_token(rsa_key, aud="someone-else"))

    @pytest.mark.asyncio
    async def test_wrong_issuer_rejected(self, verifier, rsa_key):
        with pytest.raises(InvalidTokenError):
            await verifier.verify(_make_token(rsa_key, iss="https://evil.example"))

    @pytest.mark.asyncio
    async def test_wrong_signing_key_rejected(self, oidc_config, rsa_key):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        v = OIDCVerifier(config=oidc_config)
        with patch.object(
            OIDCVerifier, "_resolve_signing_key", return_value=other.public_key()
        ):
            with pytest.raises(InvalidTokenError):
                await v.verify(_make_token(rsa_key))

    @pytest.mark.asyncio
    async def test_jwks_resolution_failure_is_wrapped(self, oidc_config, rsa_key):
        v = OIDCVerifier(config=oidc_config)
        with patch.object(
            OIDCVerifier,
            "_resolve_signing_key",
            side_effect=RuntimeError("network down"),
        ):
            with pytest.raises(InvalidTokenError, match="signing key"):
                await v.verify(_make_token(rsa_key))

    @pytest.mark.asyncio
    async def test_scope_claim_as_array(self, verifier, rsa_key):
        user = await verifier.verify(
            _make_token(rsa_key, scope=["webhooks:write", "metrics:read"])
        )
        assert user.has_scope("webhooks:write")


class TestAuthManagerFallback:
    @pytest.mark.asyncio
    async def test_bearer_falls_back_to_oidc(self, oidc_config, rsa_key):
        from core.auth.manager import AuthManager

        with patch("core.auth.jwt.create_redis_client") as factory:
            redis = AsyncMock()
            redis.get.return_value = None
            factory.return_value = redis
            mgr = AuthManager(config=oidc_config)

        # An IdP token is not a valid local HS256 token → local verify fails →
        # OIDC path accepts it.
        token = _make_token(rsa_key)
        with patch.object(
            OIDCVerifier, "_resolve_signing_key", return_value=rsa_key.public_key()
        ):
            user = await mgr.authenticate(f"Bearer {token}")
        assert user.is_authenticated
        assert user.user_id == "okta|123"

    @pytest.mark.asyncio
    async def test_local_token_still_works_with_oidc_enabled(self, oidc_config):
        from core.auth.manager import AuthManager

        with patch("core.auth.jwt.create_redis_client") as factory:
            redis = AsyncMock()
            redis.get.return_value = None
            factory.return_value = redis
            mgr = AuthManager(config=oidc_config)
            token = await mgr.create_token("local-user", roles={AuthRole.USER})
            user = await mgr.authenticate(f"Bearer {token}")
        # Local path wins without ever touching OIDC.
        assert user.user_id == "local-user"

    @pytest.mark.asyncio
    async def test_invalid_bearer_returns_anonymous(self, oidc_config, rsa_key):
        from core.auth.manager import AuthManager

        with patch("core.auth.jwt.create_redis_client") as factory:
            redis = AsyncMock()
            redis.get.return_value = None
            factory.return_value = redis
            mgr = AuthManager(config=oidc_config)
        with patch.object(
            OIDCVerifier,
            "_resolve_signing_key",
            side_effect=RuntimeError("no key"),
        ):
            user = await mgr.authenticate("Bearer not-a-real-token")
        assert not user.is_authenticated
