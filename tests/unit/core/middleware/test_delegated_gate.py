"""RFC 8693 agent-delegated identities at the coarse role gate.

A delegated token keeps the user's roles for audit, but authority-wise it is a
capability identity: `require_admin` must not grant control-plane access on
the user's role alone. Split from test_security_auth.py for the 500-line cap.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.auth.types import AuthRole, AuthUser
from core.middleware.security import SecurityManager

AGENT_ACT = {"sub": "agent-1", "client_id": "agent-1"}
IMPERSONATION_ACT = {"sub": "admin-user"}  # no client_id -> impersonation


def _delegated_user(**overrides) -> AuthUser:
    defaults = dict(
        user_id="alice",
        roles={AuthRole.ADMIN},
        scopes={"chat:read"},
        metadata={"act": AGENT_ACT},
    )
    defaults.update(overrides)
    return AuthUser(**defaults)


class TestCoarseGate:
    """`require_admin` matches roles only; a narrowly-scoped delegated token
    for an admin user must not pass the control-plane gate on the user's role."""

    def _request(self):
        request = MagicMock()
        request.headers = {"Authorization": "Bearer tok"}
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

    def _auth_returning(self, user):
        mock_auth = AsyncMock()
        mock_auth.authenticate.return_value = user
        return mock_auth

    async def test_delegated_admin_denied_on_admin_gate(self, mock_security_config):
        manager = self._manager(mock_security_config)
        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_get_auth.return_value = self._auth_returning(_delegated_user())
            with pytest.raises(HTTPException) as exc:
                await manager.enforce_auth(
                    self._request(), allowed_roles={"admin"}, limit_per_minute=10
                )
            assert exc.value.status_code == 403

    async def test_delegated_identity_admitted_on_data_tier(self, mock_security_config):
        manager = self._manager(mock_security_config)
        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_get_auth.return_value = self._auth_returning(_delegated_user())
            resolved = await manager.enforce_auth(
                self._request(),
                allowed_roles={"user", "admin", "job", "scoped"},
                limit_per_minute=10,
            )
            assert resolved == "scoped"

    async def test_impersonation_admin_still_passes_admin_gate(
        self, mock_security_config
    ):
        manager = self._manager(mock_security_config)
        user = _delegated_user(metadata={"act": IMPERSONATION_ACT, "imp": True})
        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_get_auth.return_value = self._auth_returning(user)
            resolved = await manager.enforce_auth(
                self._request(), allowed_roles={"admin"}, limit_per_minute=10
            )
            assert resolved == "admin"
