"""A presented credential that resolves to the anonymous identity is a failed
guess and must be charged to the per-IP authfail window.

``AuthManager.authenticate()`` reports a bad bearer token or API key by
returning the anonymous identity rather than raising ``AuthError``, so the
throttle in the ``except AuthError`` branch alone never fired on REST routes.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.middleware.security import SecurityManager


class TestAnonymousResolutionThrottle:
    def _manager(self, mock_security_config):
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis_factory.return_value = AsyncMock()
            manager = SecurityManager(mock_security_config)
        manager.rate_limiter = AsyncMock()
        return manager

    def _bad_cred_request(self):
        request = MagicMock()
        request.headers = {"Authorization": "Bearer bogus"}
        request.client.host = "203.0.113.9"
        request.url.path = "/secure"
        request.state = MagicMock()
        request.state._auth_memo = None
        return request

    def _anonymous_user(self):
        user = MagicMock()
        user.is_authenticated = False
        return user

    @pytest.mark.asyncio
    async def test_credential_resolving_to_anonymous_is_throttled(
        self, mock_security_config
    ):
        """AuthManager.authenticate() reports a bad bearer token or API key by
        returning the anonymous identity, not by raising. That path must be
        charged to the same per-IP authfail window."""
        manager = self._manager(mock_security_config)
        request = self._bad_cred_request()

        with (
            patch("core.auth.manager.get_auth_manager") as mock_get_auth,
            patch("core.middleware.security.get_audit_logger") as mock_audit,
        ):
            mock_auth = AsyncMock()
            mock_auth.authenticate.return_value = self._anonymous_user()
            mock_get_auth.return_value = mock_auth
            mock_audit.return_value.log = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await manager.enforce_auth(
                    request, allowed_roles={"user"}, limit_per_minute=10
                )
            assert exc.value.status_code == 401

        manager.rate_limiter.check.assert_awaited_once_with(
            "authfail:203.0.113.9",
            mock_security_config.auth_failure_limit_per_minute,
            mock_security_config.rate_limit_window_seconds,
        )

    @pytest.mark.asyncio
    async def test_throttle_trips_before_audit_write(self, mock_security_config):
        manager = self._manager(mock_security_config)
        manager.rate_limiter.check = AsyncMock(
            side_effect=HTTPException(status_code=429, detail="rate limited")
        )
        request = self._bad_cred_request()

        with (
            patch("core.auth.manager.get_auth_manager") as mock_get_auth,
            patch("core.middleware.security.get_audit_logger") as mock_audit,
        ):
            mock_auth = AsyncMock()
            mock_auth.authenticate.return_value = self._anonymous_user()
            mock_get_auth.return_value = mock_auth
            mock_audit.return_value.log = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await manager.enforce_auth(
                    request, allowed_roles={"user"}, limit_per_minute=10
                )
            assert exc.value.status_code == 429
            mock_audit.return_value.log.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_credential_is_not_charged(self, mock_security_config):
        manager = self._manager(mock_security_config)
        request = self._bad_cred_request()
        request.headers = {}

        with (
            patch("core.auth.manager.get_auth_manager") as mock_get_auth,
            patch("core.middleware.security.get_audit_logger") as mock_audit,
        ):
            mock_auth = AsyncMock()
            mock_auth.authenticate.return_value = self._anonymous_user()
            mock_get_auth.return_value = mock_auth
            mock_audit.return_value.log = AsyncMock()

            with pytest.raises(HTTPException) as exc:
                await manager.enforce_auth(
                    request, allowed_roles={"user"}, limit_per_minute=10
                )
            assert exc.value.status_code == 401

        manager.rate_limiter.check.assert_not_awaited()
