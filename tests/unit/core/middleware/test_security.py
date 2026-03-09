"""
Tests for Security Middleware and Logic.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
from core.middleware.security import (
    SecurityManager,
    RateLimiter,
    SecurityHeadersMiddleware,
)
from core.config import SecurityConfig


@pytest.fixture
def mock_security_config():
    config = MagicMock(spec=SecurityConfig)
    config.secret_key = "test-secret"
    config.admin_pass = "admin123"
    config.admin_pass_hashed = None
    config.api_keys_admin = {"key-admin"}
    config.api_keys_job = {"key-job"}
    config.api_keys_user = {"key-user"}
    config.auth_required = True
    config.rate_limit_window_seconds = 60
    config.rate_limit_user_per_minute = 10
    config.rate_limit_admin_per_minute = 100
    config.rate_limit_job_per_minute = 100
    config.security_headers_enabled = True
    config.content_security_policy = "default-src 'self'"
    config.enable_hsts = False
    return config


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_allows_requests_within_limit(self):
        with patch(
            "core.middleware.security.create_redis_client"
        ) as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.incr.return_value = 1
            mock_redis_factory.return_value = mock_redis

            limiter = RateLimiter()
            for i in range(5):
                await limiter.check("id1", limit=10, window_seconds=60)

            assert mock_redis.incr.call_count == 5

    @pytest.mark.asyncio
    async def test_blocks_requests_over_limit(self):
        with patch(
            "core.middleware.security.create_redis_client"
        ) as mock_redis_factory:
            mock_redis = AsyncMock()
            # First 5 calls return 1, 2, 3, 4, 5
            # 6th call returns 6
            mock_redis.incr.side_effect = [1, 2, 3, 4, 5, 6]
            mock_redis_factory.return_value = mock_redis

            limiter = RateLimiter()
            for i in range(5):
                await limiter.check("id2", limit=5, window_seconds=60)

            with pytest.raises(HTTPException) as exc:
                await limiter.check("id2", limit=5, window_seconds=60)
            assert exc.value.status_code == 429


class TestSecurityManager:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["user", "admin"])
    async def test_enforce_auth_valid_key(self, mock_security_config, role):
        with patch(
            "core.middleware.security.create_redis_client"
        ) as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.incr.return_value = 1
            mock_redis_factory.return_value = mock_redis

            manager = SecurityManager(mock_security_config)
            request = MagicMock()
            request.headers = {"X-API-Key": f"key-{role}"}
            request.client.host = "1.2.3.4"

        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_auth = AsyncMock()
            mock_user = MagicMock()
            mock_user.is_authenticated = True
            mock_user.roles = {MagicMock(value=role)}
            mock_user.user_id = "test-user"
            mock_auth.authenticate.return_value = mock_user
            mock_get_auth.return_value = mock_auth

            resolved_role = await manager.enforce_auth(
                request, allowed_roles={role}, limit_per_minute=10
            )
            assert resolved_role == role

    @pytest.mark.asyncio
    async def test_enforce_auth_missing_key(self, mock_security_config):
        with patch(
            "core.middleware.security.create_redis_client"
        ) as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.incr.return_value = 1
            mock_redis_factory.return_value = mock_redis

            manager = SecurityManager(mock_security_config)
            request = MagicMock()
            request.headers = {}

        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_auth = AsyncMock()
            mock_user = MagicMock()
            mock_user.is_authenticated = False
            mock_auth.authenticate.return_value = mock_user
            mock_get_auth.return_value = mock_auth

            with pytest.raises(HTTPException) as exc:
                await manager.enforce_auth(
                    request, allowed_roles={"user"}, limit_per_minute=10
                )
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_enforce_auth_forbidden_role(self, mock_security_config):
        with patch(
            "core.middleware.security.create_redis_client"
        ) as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.incr.return_value = 1
            mock_redis_factory.return_value = mock_redis

            manager = SecurityManager(mock_security_config)
            request = MagicMock()
            request.headers = {"X-API-Key": "key-user"}

        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_auth = AsyncMock()
            mock_user = MagicMock()
            mock_user.is_authenticated = True
            mock_user.roles = {MagicMock(value="user")}
            mock_auth.authenticate.return_value = mock_user
            mock_get_auth.return_value = mock_auth

            # Only admin allowed
            with pytest.raises(HTTPException) as exc:
                await manager.enforce_auth(
                    request, allowed_roles={"admin"}, limit_per_minute=10
                )
            assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_security_headers_middleware(mock_security_config):
    app = MagicMock()
    middleware = SecurityHeadersMiddleware(app, config=mock_security_config)
    request = MagicMock()

    async def call_next(req):
        response = MagicMock()
        response.headers = {}
        return response

    response = await middleware.dispatch(request, call_next)
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"
