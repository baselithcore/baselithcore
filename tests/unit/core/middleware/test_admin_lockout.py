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

        with (
            patch(
                "core.middleware._admin_lockout._is_production_env", return_value=True
            ),
            patch(
                "core.middleware._admin_lockout._redis_backend_declared",
                return_value=True,
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await manager.check_admin_lockout("203.0.113.7")
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_redis_failure_without_declared_backend_stays_available(
        self, mock_security_config
    ):
        """The lazily built client exists even where Redis was never declared;
        its failure there must fall back, not 503 every admin login."""
        manager = self._manager(mock_security_config)
        failing_redis = MagicMock()
        failing_redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
        manager.rate_limiter._redis = failing_redis

        with (
            patch(
                "core.middleware._admin_lockout._is_production_env", return_value=True
            ),
            patch(
                "core.middleware._admin_lockout._redis_backend_declared",
                return_value=False,
            ),
        ):
            await manager.check_admin_lockout("203.0.113.7")

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


class TestRecordFailureAtomic:
    """The failure step is ONE Redis round trip (a Lua script), not an
    INCR followed by one or two EXPIRE calls — the gap between those left a
    counter without a TTL (a permanent lockout for that IP) whenever the
    process died in between."""

    def _manager(self, mock_security_config):
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis_factory.return_value = None
        return SecurityManager(mock_security_config)

    @pytest.mark.asyncio
    async def test_uses_registered_script_once(self, mock_security_config):
        manager = self._manager(mock_security_config)
        script = AsyncMock(return_value=1)
        redis = MagicMock()
        redis.register_script = MagicMock(return_value=script)
        redis.incr = AsyncMock(side_effect=AssertionError("non-atomic INCR used"))
        redis.expire = AsyncMock(side_effect=AssertionError("non-atomic EXPIRE used"))
        manager.rate_limiter._redis = redis

        await manager.record_admin_failure("203.0.113.9")
        await manager.record_admin_failure("203.0.113.9")

        # Registered once, invoked per failure with the key and the three
        # policy constants (window, lockout duration, threshold).
        redis.register_script.assert_called_once()
        assert script.await_count == 2
        kwargs = script.await_args.kwargs
        assert kwargs["keys"] == [
            f"{manager.rate_limiter._prefix}admin_lockout:203.0.113.9"
        ]
        assert kwargs["args"] == [
            manager._LOCKOUT_WINDOW_SECONDS,
            manager._LOCKOUT_DURATION_SECONDS,
            manager._LOCKOUT_MAX_FAILURES,
        ]

    @pytest.mark.asyncio
    async def test_plain_eval_when_client_cannot_register(self, mock_security_config):
        manager = self._manager(mock_security_config)
        redis = MagicMock(spec=["eval"])
        redis.eval = AsyncMock(return_value=5)
        manager.rate_limiter._redis = redis

        await manager.record_admin_failure("203.0.113.10")

        redis.eval.assert_awaited_once()
        script, numkeys, key, *args = redis.eval.await_args.args
        assert numkeys == 1 and key.endswith("admin_lockout:203.0.113.10")
        assert args == [
            manager._LOCKOUT_WINDOW_SECONDS,
            manager._LOCKOUT_DURATION_SECONDS,
            manager._LOCKOUT_MAX_FAILURES,
        ]
        # The script arms the window on first hit, heals a TTL-less key and
        # stretches to the lockout duration at the threshold.
        assert "INCR" in script and "TTL" in script and "EXPIRE" in script

    @pytest.mark.asyncio
    async def test_script_failure_counts_in_memory(self, mock_security_config):
        manager = self._manager(mock_security_config)
        redis = MagicMock(spec=["eval"])
        redis.eval = AsyncMock(side_effect=RuntimeError("redis down"))
        manager.rate_limiter._redis = redis

        await manager.record_admin_failure("203.0.113.11")

        count, _ = manager._lockout_fallback["203.0.113.11"]
        assert count == 1
