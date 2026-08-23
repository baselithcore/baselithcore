from unittest.mock import AsyncMock, patch

import pytest

from core.auth.manager import AuthManager, get_auth_manager
from core.auth.types import AuthRole, AuthUser, InsufficientPermissionsError
from core.config.security import SecurityConfig


@pytest.fixture
def security_config():
    return SecurityConfig(
        SECRET_KEY="test-secret-with-at-least-thirty-two-chars",
        API_KEYS_USER={"user-key"},
        API_KEYS_ADMIN={"admin-key"},
    )


class TestAuthManager:
    @pytest.fixture
    def auth_manager(self, security_config):
        with patch("core.auth.jwt.create_redis_client") as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis
            return AuthManager(config=security_config)

    @pytest.mark.asyncio
    async def test_authenticate_bearer_success(self, auth_manager):
        token = await auth_manager.create_token("user123", roles={AuthRole.USER})
        user = await auth_manager.authenticate(f"Bearer {token}")

        assert user.is_authenticated
        assert user.user_id == "user123"
        assert AuthRole.USER in user.roles

    @pytest.mark.asyncio
    async def test_authenticate_bearer_invalid(self, auth_manager):
        user = await auth_manager.authenticate("Bearer invalid-token")
        assert not user.is_authenticated
        assert user.user_id == "anonymous"

    @pytest.mark.asyncio
    async def test_authenticate_api_key_success(self, auth_manager):
        # APIKeyValidator loads from config in __init__
        user = await auth_manager.authenticate("ApiKey user-key")
        assert user.is_authenticated
        assert AuthRole.USER in user.roles

    @pytest.mark.asyncio
    async def test_authenticate_api_key_invalid(self, auth_manager):
        user = await auth_manager.authenticate("ApiKey wrong-key")
        assert not user.is_authenticated

    @pytest.mark.asyncio
    async def test_authenticate_no_header(self, auth_manager):
        user = await auth_manager.authenticate(None)
        assert not user.is_authenticated

    @pytest.mark.asyncio
    async def test_require_auth_decorator_async(self, auth_manager):
        @auth_manager.require_auth({AuthRole.ADMIN})
        async def protected_func(user: AuthUser):
            return "success"

        # Case 1: Insufficient roles
        user_low = AuthUser(user_id="u", roles={AuthRole.USER})
        with pytest.raises(InsufficientPermissionsError):
            await protected_func(user=user_low)

        # Case 2: Sufficient roles
        user_admin = AuthUser(user_id="a", roles={AuthRole.ADMIN})
        assert await protected_func(user=user_admin) == "success"

        # Case 3: Unauthenticated
        anon = AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})
        with pytest.raises(InsufficientPermissionsError):
            await protected_func(user=anon)

    def test_require_auth_decorator_sync(self, auth_manager):
        @auth_manager.require_auth()
        def protected_sync(user: AuthUser):
            return "ok"

        user = AuthUser(user_id="u", roles={AuthRole.USER})
        assert protected_sync(user=user) == "ok"

        anon = AuthUser(user_id="a", roles={AuthRole.ANONYMOUS})
        with pytest.raises(InsufficientPermissionsError):
            protected_sync(user=anon)

    def test_get_auth_manager_singleton(self):
        m1 = get_auth_manager()
        m2 = get_auth_manager()
        assert m1 is m2


class TestJWTHandlerAndAPIKeys:
    # Testing the underlying components briefly to ensure coverage
    @pytest.mark.asyncio
    async def test_jwt_create_and_verify(self):
        from core.auth.jwt import JWTHandler

        with patch("core.auth.jwt.create_redis_client") as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis

            handler = JWTHandler(secret_key="secret-with-at-least-thirty-two-chars")
            token = handler.create_token(
                "u1", roles={AuthRole.USER}, extra_claims={"custom": "val"}
            )
            user = await handler.verify_token(token)
            assert user.user_id == "u1"
            assert user.metadata["custom"] == "val"

    @pytest.mark.asyncio
    async def test_tenant_id_not_forgeable_via_extra_claims(self):
        """tenant_id is a reserved claim: it can only be set via the first-class
        parameter, never injected/overridden through extra_claims."""
        from core.auth.jwt import JWTHandler

        with patch("core.auth.jwt.create_redis_client") as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis
            handler = JWTHandler(secret_key="secret-with-at-least-thirty-two-chars")

            # A caller-supplied tenant_id in extra_claims must be dropped…
            injected = handler.create_token(
                "u1", roles={AuthRole.USER}, extra_claims={"tenant_id": "evil-tenant"}
            )
            user = await handler.verify_token(injected)
            assert user.tenant_id == "default"

            # …while the first-class parameter sets it.
            legit = handler.create_token("u1", roles={AuthRole.USER}, tenant_id="acme")
            assert (await handler.verify_token(legit)).tenant_id == "acme"

    @pytest.mark.asyncio
    async def test_rotate_refresh_token_preserves_roles_and_tenant(self):
        from core.auth.jwt import JWTHandler

        with patch("core.auth.jwt.create_redis_client") as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis

            handler = JWTHandler(secret_key="secret-with-at-least-thirty-two-chars")
            refresh_token = handler.create_refresh_token(
                "admin1",
                roles={AuthRole.ADMIN, AuthRole.USER},
                tenant_id="tenant-123",
            )

            new_access, new_refresh = await handler.rotate_refresh_token(refresh_token)

            access_user = await handler.verify_token(new_access)
            # A refresh token must be verified with expected_type="refresh";
            # the default access path now rejects it (see test below).
            refresh_user = await handler.verify_token(
                new_refresh, expected_type="refresh"
            )

            assert access_user.user_id == "admin1"
            assert access_user.tenant_id == "tenant-123"
            assert AuthRole.ADMIN in access_user.roles
            assert refresh_user.tenant_id == "tenant-123"
            assert AuthRole.ADMIN in refresh_user.roles
            assert refresh_user.metadata["type"] == "refresh"

    @pytest.mark.asyncio
    async def test_refresh_token_rejected_on_access_path(self):
        """A refresh token must not authenticate as a bearer access token."""
        from core.auth.jwt import JWTHandler
        from core.auth.types import InvalidTokenError

        with patch("core.auth.jwt.create_redis_client") as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis

            handler = JWTHandler(secret_key="secret-with-at-least-thirty-two-chars")
            refresh_token = handler.create_refresh_token(
                "admin1", roles={AuthRole.ADMIN}, tenant_id="t1"
            )

            # Default access-path verification rejects it.
            with pytest.raises(InvalidTokenError):
                await handler.verify_token(refresh_token)

            # And it stays rejected on a second call (cache path also gated).
            with pytest.raises(InvalidTokenError):
                await handler.verify_token(refresh_token)

    @pytest.mark.asyncio
    async def test_jwt_accepts_secretstr_key(self):
        from pydantic import SecretStr

        from core.auth.jwt import JWTHandler

        with patch("core.auth.jwt.create_redis_client") as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis

            handler = JWTHandler(
                secret_key=SecretStr("secret-with-at-least-thirty-two-chars")
            )
            token = handler.create_token("u1", roles={AuthRole.USER})
            user = await handler.verify_token(token)
            assert user.user_id == "u1"

    @pytest.mark.asyncio
    async def test_jwt_rejects_token_without_exp(self):
        """A token missing `exp` would never expire and could not be revoked."""
        import jwt as pyjwt

        from core.auth.jwt import JWTHandler
        from core.auth.types import InvalidTokenError

        with patch("core.auth.jwt.create_redis_client") as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis

            secret = "secret-with-at-least-thirty-two-chars"
            handler = JWTHandler(secret_key=secret)
            immortal = pyjwt.encode(
                {"sub": "u1", "roles": ["user"]}, secret, algorithm="HS256"
            )
            with pytest.raises(InvalidTokenError) as exc_info:
                await handler.verify_token(immortal)

            # The rejection reason is no longer in the message — that string
            # reaches the client as the 401 detail and would tell a forger
            # which claim to add next. It survives on __cause__ (and in the
            # jwt_verification_failed log record) for diagnostics.
            assert str(exc_info.value) == "Invalid token"
            assert isinstance(exc_info.value.__cause__, pyjwt.MissingRequiredClaimError)
            assert "exp" in str(exc_info.value.__cause__)

    def test_jwt_rejects_none_algorithm(self):
        from core.auth.jwt import JWTHandler

        with patch("core.auth.jwt.create_redis_client") as mock_redis_factory:
            mock_redis_factory.return_value = AsyncMock()
            for bad in ("none", "None", "NONE", ""):
                with pytest.raises(ValueError, match="not allowed"):
                    JWTHandler(
                        secret_key="secret-with-at-least-thirty-two-chars",
                        algorithm=bad,
                    )

    @pytest.mark.asyncio
    async def test_api_key_validator_revocation(self, security_config):
        from uuid import uuid4

        from core.auth.api_keys import APIKeyValidator

        validator = APIKeyValidator(config=security_config)

        # Unique per run: revocation tombstones are PERSISTENT (Redis, no
        # TTL), so a fixed literal would stay denied across test runs when a
        # real Redis is reachable.
        key = f"new-key-{uuid4().hex}"
        validator.register_key(key, "new-user")
        user = await validator.validate_key(key)
        assert user is not None

        await validator.revoke_key(key)
        user = await validator.validate_key(key)
        assert user is None

        # Re-trusting the same key value requires the explicit operator
        # action (reinstate) plus re-registration.
        await validator.reinstate_key(key)
        validator.register_key(key, "new-user")
        user = await validator.validate_key(key)
        assert user is not None
