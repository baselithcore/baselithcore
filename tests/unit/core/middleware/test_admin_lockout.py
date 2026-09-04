"""Tests for admin Basic-auth lockout (AdminLockoutMixin on SecurityManager).

Extracted from ``test_security_auth.py`` to keep both modules under the
500-line cap. The lockout logic lives in ``core.middleware._admin_lockout``, so
the production-env guard is patched there (not on ``core.middleware.security``).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.middleware.security import SecurityManager


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

    @pytest.mark.asyncio
    async def test_redis_failure_fails_closed_in_production(self, mock_security_config):
        """A degraded Redis must not grant unthrottled brute-force in prod:
        per-replica memory is defeated by rotating replicas, so privileged auth
        is refused (503) instead of silently downgrading the control."""
        manager = self._manager(mock_security_config)
        failing_redis = MagicMock()
        failing_redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
        manager.rate_limiter._redis = failing_redis

        with patch(
            "core.middleware._admin_lockout._is_production_env", return_value=True
        ):
            with pytest.raises(HTTPException) as exc:
                await manager.check_admin_lockout("203.0.113.7")
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_redis_failure_falls_back_outside_production(
        self, mock_security_config
    ):
        """Outside production the in-memory fallback keeps dev frictionless."""
        manager = self._manager(mock_security_config)
        failing_redis = MagicMock()
        failing_redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
        manager.rate_limiter._redis = failing_redis

        with patch(
            "core.middleware._admin_lockout._is_production_env", return_value=False
        ):
            await manager.check_admin_lockout("203.0.113.7")  # must not raise

    @pytest.mark.asyncio
    async def test_explicit_fail_open_opt_out(self, mock_security_config):
        """BASELITH_LOCKOUT_FAIL_OPEN=true prefers availability explicitly."""
        manager = self._manager(mock_security_config)
        failing_redis = MagicMock()
        failing_redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
        manager.rate_limiter._redis = failing_redis

        with (
            patch(
                "core.middleware._admin_lockout._is_production_env", return_value=True
            ),
            patch.dict("os.environ", {"BASELITH_LOCKOUT_FAIL_OPEN": "true"}),
        ):
            await manager.check_admin_lockout("203.0.113.7")  # must not raise


class TestNoSharedCounterAtAll:
    """``_redis is None`` is the *permanent* loss of the shared counter.

    A Redis unreachable when the limiter was constructed leaves the client
    None for the life of the process, so the except-branch above never runs and
    the lockout silently degraded to per-replica memory — defeated by rotating
    replicas, which is exactly the case the fail-closed posture exists for.
    """

    def _manager(self, mock_security_config):
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis_factory.return_value = None
            manager = SecurityManager(mock_security_config)
        manager.rate_limiter._redis = None
        return manager

    @pytest.mark.asyncio
    async def test_production_with_redis_declared_fails_closed(
        self, mock_security_config
    ):
        manager = self._manager(mock_security_config)
        manager.rate_limiter._redis = None

        with patch(
            "core.middleware._admin_lockout._is_production_env", return_value=True
        ):
            with patch(
                "core.middleware._admin_lockout._redis_backend_declared",
                return_value=True,
            ):
                with pytest.raises(HTTPException) as exc:
                    await manager.check_admin_lockout("10.0.0.1")
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_production_without_redis_configured_stays_available(
        self, mock_security_config
    ):
        """No Redis declared means the in-process counter is the design, not a
        degraded state — refusing every privileged login would be an outage we
        inflicted on ourselves."""
        manager = self._manager(mock_security_config)
        manager.rate_limiter._redis = None

        with patch(
            "core.middleware._admin_lockout._is_production_env", return_value=True
        ):
            with patch(
                "core.middleware._admin_lockout._redis_backend_declared",
                return_value=False,
            ):
                await manager.check_admin_lockout("10.0.0.2")

    @pytest.mark.asyncio
    async def test_explicit_opt_out_restores_availability(self, mock_security_config):
        manager = self._manager(mock_security_config)
        manager.rate_limiter._redis = None

        with patch(
            "core.middleware._admin_lockout._is_production_env", return_value=True
        ):
            with patch(
                "core.middleware._admin_lockout._redis_backend_declared",
                return_value=True,
            ):
                with patch(
                    "core.middleware._admin_lockout._lockout_fail_open",
                    return_value=True,
                ):
                    await manager.check_admin_lockout("10.0.0.3")

    @pytest.mark.asyncio
    async def test_outside_production_never_refuses(self, mock_security_config):
        manager = self._manager(mock_security_config)
        manager.rate_limiter._redis = None

        with patch(
            "core.middleware._admin_lockout._is_production_env", return_value=False
        ):
            await manager.check_admin_lockout("10.0.0.4")
