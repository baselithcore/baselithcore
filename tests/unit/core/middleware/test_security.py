"""
Tests for Security Middleware and Logic.
"""

import hashlib
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
    config.frame_options = "DENY"
    config.content_security_policy = "default-src 'self'"
    config.permissions_policy = None
    config.enable_hsts = False
    config.hsts_max_age = 31536000
    return config


class TestRateLimiter:
    @staticmethod
    def _mock_redis_with_script(script: AsyncMock) -> AsyncMock:
        """Redis mock whose register_script returns the given Lua-script stub."""
        mock_redis = AsyncMock()
        mock_redis.register_script = MagicMock(return_value=script)
        return mock_redis

    @pytest.mark.asyncio
    async def test_allows_requests_within_limit(self):
        with patch(
            "core.middleware.security.create_redis_client"
        ) as mock_redis_factory:
            script = AsyncMock(return_value=1)
            mock_redis_factory.return_value = self._mock_redis_with_script(script)

            limiter = RateLimiter()
            for i in range(5):
                await limiter.check("id1", limit=10, window_seconds=60)

            # One atomic Lua call per check (single Redis round trip).
            assert script.await_count == 5

    @pytest.mark.asyncio
    async def test_blocks_requests_over_limit(self):
        with patch(
            "core.middleware.security.create_redis_client"
        ) as mock_redis_factory:
            # Counter returned by the Lua script: 1..5 allowed, 6 over limit.
            script = AsyncMock(side_effect=[1, 2, 3, 4, 5, 6])
            mock_redis_factory.return_value = self._mock_redis_with_script(script)

            limiter = RateLimiter()
            for i in range(5):
                await limiter.check("id2", limit=5, window_seconds=60)

            with pytest.raises(HTTPException) as exc:
                await limiter.check("id2", limit=5, window_seconds=60)
            assert exc.value.status_code == 429

    @pytest.mark.asyncio
    async def test_falls_back_to_memory_when_redis_fails(self):
        with patch(
            "core.middleware.security.create_redis_client"
        ) as mock_redis_factory:
            script = AsyncMock(side_effect=RuntimeError("redis down"))
            mock_redis_factory.return_value = self._mock_redis_with_script(script)

            limiter = RateLimiter()
            await limiter.check("id3", limit=2, window_seconds=60)
            await limiter.check("id3", limit=2, window_seconds=60)
            with pytest.raises(HTTPException) as exc:
                await limiter.check("id3", limit=2, window_seconds=60)

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
    async def test_enforce_auth_hashes_api_key_for_rate_limit(
        self, mock_security_config
    ):
        with patch(
            "core.middleware.security.create_redis_client"
        ) as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis_factory.return_value = mock_redis

            manager = SecurityManager(mock_security_config)
            manager.rate_limiter.check = AsyncMock()
            request = MagicMock()
            request.headers = {"X-API-Key": "key-user"}
            request.client.host = "1.2.3.4"
            request.url.path = "/secure"
            request.state = MagicMock()

        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_auth = AsyncMock()
            mock_user = MagicMock()
            mock_user.is_authenticated = True
            mock_user.roles = {MagicMock(value="user")}
            mock_user.user_id = "test-user"
            mock_user.tenant_id = "tenant-a"
            mock_auth.authenticate.return_value = mock_user
            mock_get_auth.return_value = mock_auth

            await manager.enforce_auth(
                request, allowed_roles={"user"}, limit_per_minute=10
            )

        identifier = manager.rate_limiter.check.await_args.args[0]
        assert "key-user" not in identifier
        assert identifier == f"user:api:{hashlib.sha256(b'key-user').hexdigest()}"


async def _run_security_headers_middleware(
    middleware: SecurityHeadersMiddleware,
    path: str = "/",
) -> dict[str, str]:
    """Drive the ASGI middleware end-to-end and return the merged header map."""

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware.app = downstream
    sent: list = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    await middleware(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {k.decode(): v.decode() for k, v in start["headers"]}


@pytest.mark.asyncio
async def test_security_headers_middleware(mock_security_config):
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    headers = await _run_security_headers_middleware(middleware)
    assert headers["x-frame-options"] == "DENY"
    assert headers["content-security-policy"] == "default-src 'self'"


@pytest.mark.asyncio
async def test_security_headers_middleware_sets_default_csp(mock_security_config):
    mock_security_config.content_security_policy = None
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    headers = await _run_security_headers_middleware(middleware)
    assert "content-security-policy" in headers
    assert "default-src 'self'" in headers["content-security-policy"]


@pytest.mark.asyncio
async def test_docs_routes_get_relaxed_csp(mock_security_config):
    """Swagger UI / ReDoc pages must allow the jsDelivr CDN + inline bootstrap."""
    mock_security_config.content_security_policy = None
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
        headers = await _run_security_headers_middleware(middleware, path=path)
        csp = headers["content-security-policy"]
        assert "https://cdn.jsdelivr.net" in csp
        assert "'unsafe-inline'" in csp.split("script-src", 1)[1].split(";", 1)[0]


@pytest.mark.asyncio
async def test_non_docs_routes_keep_strict_csp(mock_security_config):
    """Every non-docs route keeps the strict script-src 'self' policy."""
    mock_security_config.content_security_policy = None
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    for path in ("/", "/console", "/chat", "/documentation"):
        csp = (await _run_security_headers_middleware(middleware, path=path))[
            "content-security-policy"
        ]
        assert "cdn.jsdelivr.net" not in csp
        assert "script-src 'self';" in csp


@pytest.mark.asyncio
async def test_operator_csp_override_wins_on_docs(mock_security_config):
    """An explicit operator CSP is never overridden, even on docs routes."""
    mock_security_config.content_security_policy = "default-src 'none'"
    middleware = SecurityHeadersMiddleware(MagicMock(), config=mock_security_config)
    headers = await _run_security_headers_middleware(middleware, path="/docs")
    assert headers["content-security-policy"] == "default-src 'none'"
