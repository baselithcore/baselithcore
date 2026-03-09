import pytest
from core.auth.manager import AuthManager, get_auth_manager
from core.auth.types import AuthRole, AuthUser, InsufficientPermissionsError
from core.config.security import SecurityConfig
from unittest.mock import patch, AsyncMock


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
    async def test_api_key_validator_revocation(self, security_config):
        from core.auth.api_keys import APIKeyValidator

        validator = APIKeyValidator(config=security_config)

        # Internal register_key uses hash
        validator.register_key("new-key", "new-user")
        user = await validator.validate_key("new-key")
        assert user is not None

        await validator.revoke_key("new-key")
        user = await validator.validate_key("new-key")
        assert user is None
