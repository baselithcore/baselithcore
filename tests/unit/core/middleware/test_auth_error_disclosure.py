"""A 401 must not describe *why* the credential was rejected.

PyJWT's own text ("Signature verification failed", "Audience doesn't match",
"Token is expired") tells an attacker which part of a forged token to fix next,
one probe at a time. The reason belongs in the operator's log — sanitized, so a
newline in the message cannot forge an additional audit line — and never in the
response body.

Lives in its own module rather than in ``test_security_auth.py`` to keep that
file under the 500-line cap.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.middleware.security import SecurityManager

_LEAKY = "Audience doesn't match"


def _manager(mock_security_config) -> SecurityManager:
    with patch("core.middleware.rate_limiter.create_redis_client") as mock_factory:
        mock_factory.return_value = AsyncMock()
        manager = SecurityManager(mock_security_config)
    manager.rate_limiter = AsyncMock()
    return manager


def _request() -> MagicMock:
    request = MagicMock()
    request.headers = {"Authorization": "Bearer forged"}
    request.client.host = "203.0.113.9"
    request.url.path = "/secure"
    request.state = MagicMock()
    request.state._auth_memo = None
    return request


async def _enforce_with_auth_error(manager, message: str, mock_logger_target: str):
    """Run enforce_auth against an AuthError and return (exc_info, logger mock)."""
    from core.auth.types import AuthError

    with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
        mock_auth = AsyncMock()
        mock_auth.authenticate.side_effect = AuthError(message)
        mock_get_auth.return_value = mock_auth

        with patch(mock_logger_target) as mock_logger:
            with pytest.raises(HTTPException) as exc:
                await manager.enforce_auth(
                    _request(), allowed_roles={"user"}, limit_per_minute=10
                )
    return exc, mock_logger


class TestAuthFailureDetailIsGeneric:
    @pytest.mark.asyncio
    async def test_401_body_is_generic_but_log_carries_the_reason(
        self, mock_security_config
    ):
        exc, mock_logger = await _enforce_with_auth_error(
            _manager(mock_security_config), _LEAKY, "core.middleware.security.logger"
        )

        assert exc.value.status_code == 401
        # The client learns only that it must authenticate.
        assert exc.value.detail == "Authentication required."
        assert _LEAKY not in str(exc.value.detail)
        # The operator, on the other hand, gets the full reason.
        logged = " ".join(str(a) for a in mock_logger.warning.call_args.args)
        assert _LEAKY in logged
        assert "AuthError" in logged
        assert "203.0.113.9" in logged
        assert "/secure" in logged

    @pytest.mark.asyncio
    async def test_generic_detail_matches_the_anonymous_rejection(
        self, mock_security_config
    ):
        """Both 401 paths must return the SAME body.

        A distinct wording for "credential raised an error" vs "credential
        resolved to anonymous" would re-open the oracle at a coarser grain.
        """
        manager = _manager(mock_security_config)

        with patch("core.auth.manager.get_auth_manager") as mock_get_auth:
            mock_auth = AsyncMock()
            anonymous = MagicMock()
            anonymous.is_authenticated = False
            mock_auth.authenticate.return_value = anonymous
            mock_get_auth.return_value = mock_auth

            with pytest.raises(HTTPException) as anon_exc:
                await manager.enforce_auth(
                    _request(), allowed_roles={"user"}, limit_per_minute=10
                )

        error_exc, _ = await _enforce_with_auth_error(
            _manager(mock_security_config), _LEAKY, "core.middleware.security.logger"
        )
        assert anon_exc.value.detail == error_exc.value.detail

    @pytest.mark.asyncio
    async def test_logged_reason_cannot_forge_an_audit_line(self, mock_security_config):
        _, mock_logger = await _enforce_with_auth_error(
            _manager(mock_security_config),
            "bad kid 'x'\nAUDIT | AUTH | ok | user=admin",
            "core.middleware.security.logger",
        )

        # Positional arg 2 of the audit line is the sanitized reason.
        reason = mock_logger.warning.call_args.args[2]
        assert "\n" not in reason
        assert "\\x0a" in reason
