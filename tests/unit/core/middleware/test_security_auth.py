"""
Tests for the rate limiter and SecurityManager authentication/lockout logic.
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.middleware.security import (
    RateLimiter,
    SecurityManager,
)


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
            "core.middleware.rate_limiter.create_redis_client"
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
            "core.middleware.rate_limiter.create_redis_client"
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
            "core.middleware.rate_limiter.create_redis_client"
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
            "core.middleware.rate_limiter.create_redis_client"
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
            "core.middleware.rate_limiter.create_redis_client"
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
            "core.middleware.rate_limiter.create_redis_client"
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
            "core.middleware.rate_limiter.create_redis_client"
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
        # Rate-limit key is tenant-scoped: {tenant}:{role}:api:{sha256(key)}
        assert (
            identifier == f"tenant-a:user:api:{hashlib.sha256(b'key-user').hexdigest()}"
        )


class TestScopedIdentityGate:
    """A SCOPED identity (least-privilege API key) must be admitted by the
    data-tier gate but rejected by control-plane gates, and must never be
    promoted to job/service."""

    def _scoped_request(self):
        request = MagicMock()
        request.headers = {"X-API-Key": "sk_scoped"}
        request.client.host = "1.2.3.4"
        request.url.path = "/secure"
        request.state = MagicMock()
        return request

    def _manager(self, mock_security_config):
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.incr.return_value = 1
            mock_redis_factory.return_value = mock_redis
            manager = SecurityManager(mock_security_config)
        manager.rate_limiter.check = AsyncMock()
        return manager

    def _patch_scoped_user(self):
        mock_auth = AsyncMock()
        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_user.roles = {MagicMock(value="scoped")}
        mock_user.user_id = "scoped-key"
        mock_user.tenant_id = "default"
        mock_auth.authenticate.return_value = mock_user
        return mock_auth

    @pytest.mark.asyncio
    async def test_scoped_denied_on_control_plane(self, mock_security_config):
        manager = self._manager(mock_security_config)
        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_get_auth.return_value = self._patch_scoped_user()
            with pytest.raises(HTTPException) as exc:
                await manager.enforce_auth(
                    self._scoped_request(),
                    allowed_roles={"admin", "job"},
                    limit_per_minute=10,
                )
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_scoped_admitted_on_data_tier(self, mock_security_config):
        manager = self._manager(mock_security_config)
        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_get_auth.return_value = self._patch_scoped_user()
            resolved = await manager.enforce_auth(
                self._scoped_request(),
                allowed_roles={"user", "admin", "job", "scoped"},
                limit_per_minute=10,
            )
            assert resolved == "scoped"


class TestAdminLockoutKeying:
    """Admin lockout must key on the client IP, not the guessable username,
    so an attacker cannot lock the real admin out (account-lockout DoS)."""

    def _manager(self, mock_security_config):
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis_factory.return_value = None  # force in-memory fallback
            manager = SecurityManager(mock_security_config)
        manager.rate_limiter._redis = None
        return manager

    @pytest.mark.asyncio
    async def test_lockout_is_per_ip(self, mock_security_config):
        from fastapi import HTTPException

        manager = self._manager(mock_security_config)
        attacker_ip = "203.0.113.7"

        # Attacker exceeds the failure threshold from their IP.
        for _ in range(manager._LOCKOUT_MAX_FAILURES):
            await manager.record_admin_failure(attacker_ip)

        # That IP is now locked.
        with pytest.raises(HTTPException) as exc:
            await manager.check_admin_lockout(attacker_ip)
        assert exc.value.status_code == 429

        # A different IP (the legitimate admin) is NOT locked out.
        await manager.check_admin_lockout("198.51.100.20")  # must not raise


class TestAuthMemoReuse:
    """enforce_auth reuses the quota middleware's per-request auth memo."""

    def _request_with_memo(self, memo):
        request = MagicMock()
        request.headers = {"Authorization": "Bearer tok-123"}
        request.client.host = "1.2.3.4"

        class _State:
            pass

        request.state = _State()
        if memo is not None:
            request.state._auth_memo = memo
        return request

    def _mock_user(self, role="user"):
        user = MagicMock()
        user.is_authenticated = True
        user.roles = {MagicMock(value=role)}
        user.user_id = "memo-user"
        user.tenant_id = "t1"
        return user

    @pytest.mark.asyncio
    async def test_matching_memo_skips_reauthentication(self, mock_security_config):
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.incr.return_value = 1
            mock_redis_factory.return_value = mock_redis
            manager = SecurityManager(mock_security_config)

        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_auth = AsyncMock()
            mock_get_auth.return_value = mock_auth
            user = self._mock_user()
            memo = ("Bearer tok-123", id(mock_auth), user)
            request = self._request_with_memo(memo)

            role = await manager.enforce_auth(
                request, allowed_roles={"user"}, limit_per_minute=10
            )

            assert role == "user"
            mock_auth.authenticate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_header_mismatch_reauthenticates(self, mock_security_config):
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.incr.return_value = 1
            mock_redis_factory.return_value = mock_redis
            manager = SecurityManager(mock_security_config)

        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_auth = AsyncMock()
            mock_auth.authenticate.return_value = self._mock_user()
            mock_get_auth.return_value = mock_auth
            # Memo for a DIFFERENT header — must not be trusted.
            memo = ("Bearer other-token", id(mock_auth), self._mock_user("admin"))
            request = self._request_with_memo(memo)

            role = await manager.enforce_auth(
                request, allowed_roles={"user"}, limit_per_minute=10
            )

            assert role == "user"
            mock_auth.authenticate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_manager_instance_mismatch_reauthenticates(
        self, mock_security_config
    ):
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis = AsyncMock()
            mock_redis.incr.return_value = 1
            mock_redis_factory.return_value = mock_redis
            manager = SecurityManager(mock_security_config)

        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_auth = AsyncMock()
            mock_auth.authenticate.return_value = self._mock_user()
            mock_get_auth.return_value = mock_auth
            other_manager = object()
            memo = ("Bearer tok-123", id(other_manager), self._mock_user("admin"))
            request = self._request_with_memo(memo)

            role = await manager.enforce_auth(
                request, allowed_roles={"user"}, limit_per_minute=10
            )

            assert role == "user"
            mock_auth.authenticate.assert_awaited_once()


class TestAnonymousRateLimit:
    """Auth-disabled deployments must still meter anonymous traffic per IP."""

    @pytest.mark.asyncio
    async def test_anonymous_path_is_rate_limited(self, mock_security_config):
        mock_security_config.auth_required = False
        mock_security_config.api_keys_user = set()
        mock_security_config.api_keys_admin = set()
        mock_security_config.api_keys_job = set()
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis_factory.return_value = AsyncMock()
            manager = SecurityManager(mock_security_config)
        manager.rate_limiter = AsyncMock()

        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_auth = AsyncMock()
            anon = MagicMock()
            anon.is_authenticated = False
            mock_auth.authenticate.return_value = anon
            mock_get_auth.return_value = mock_auth

            request = MagicMock()
            request.headers = {}
            request.client.host = "9.9.9.9"
            request.state = MagicMock()
            request.state._auth_memo = None

            role = await manager.enforce_auth(
                request, allowed_roles={"user"}, limit_per_minute=10
            )

        assert role == "anonymous"
        manager.rate_limiter.check.assert_awaited_once_with(
            "default:anonymous:9.9.9.9",
            10,
            mock_security_config.rate_limit_window_seconds,
        )
