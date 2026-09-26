"""Tests for the race-free admin Basic-auth sequence (core.middleware._admin_auth)."""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.middleware import _admin_auth
from core.middleware._admin_auth import (
    KDF_MAX_CONCURRENCY,
    authenticate_admin_basic,
    client_bucket,
)
from core.middleware.security import SecurityManager


class TestClientBucket:
    def test_ipv4_unchanged(self):
        assert client_bucket("203.0.113.9") == "203.0.113.9"

    def test_ipv6_collapses_to_slash_64(self):
        a = client_bucket("2001:db8:1:2:aaaa::1")
        b = client_bucket("2001:db8:1:2:ffff:ffff:ffff:ffff")
        assert a == b == "2001:db8:1:2::/64"
        assert client_bucket("2001:db8:1:3::1") != a

    def test_ipv4_mapped_ipv6_uses_embedded_ipv4(self):
        assert client_bucket("::ffff:203.0.113.9") == "203.0.113.9"

    def test_unparsable_passthrough(self):
        assert client_bucket("unknown") == "unknown"


def _manager(mock_security_config) -> SecurityManager:
    with patch("core.middleware.rate_limiter.create_redis_client") as factory:
        factory.return_value = None  # in-memory lockout fallback
        manager = SecurityManager(mock_security_config)
    manager.rate_limiter._redis = None
    return manager


@pytest.fixture
def manager(mock_security_config):
    mock_security_config.admin_user = "admin"
    m = _manager(mock_security_config)
    with patch("core.middleware.security.get_security_manager", return_value=m):
        yield m


class TestAuthenticateAdminBasic:
    @pytest.mark.asyncio
    async def test_success_clears_and_failure_records(self, manager):
        manager.verify_admin_password = MagicMock(side_effect=[False, True])
        assert await authenticate_admin_basic("203.0.113.1", "admin", "bad") is False
        assert manager._lockout_fallback["203.0.113.1"][0] == 1
        assert await authenticate_admin_basic("203.0.113.1", "admin", "good") is True
        assert "203.0.113.1" not in manager._lockout_fallback

    @pytest.mark.asyncio
    async def test_wrong_username_still_derives(self, manager):
        manager.verify_admin_password = MagicMock(return_value=True)
        assert await authenticate_admin_basic("203.0.113.1", "root", "good") is False
        manager.verify_admin_password.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_ascii_username_is_a_failure_not_an_error(self, manager):
        manager.verify_admin_password = MagicMock(return_value=False)
        assert await authenticate_admin_basic("203.0.113.1", "àdmin", "x") is False

    @pytest.mark.asyncio
    async def test_concurrent_burst_cannot_race_past_lockout(self, manager):
        """500 parallel wrong guesses from one source: the old check-then-act
        sequence let all of them through the up-front check. Now at most the
        threshold plus the in-flight slots get a derivation."""
        _admin_auth._kdf_slot = None
        derivations = 0
        lock = threading.Lock()

        def slow_wrong(_candidate: str) -> bool:
            nonlocal derivations
            with lock:
                derivations += 1
            time.sleep(0.002)
            return False

        manager.verify_admin_password = slow_wrong

        async def attempt() -> str:
            try:
                ok = await authenticate_admin_basic("203.0.113.66", "admin", "guess")
            except HTTPException as exc:
                return str(exc.status_code)
            return "ok" if ok else "401"

        results = await asyncio.gather(*(attempt() for _ in range(500)))
        assert derivations <= manager._LOCKOUT_MAX_FAILURES + KDF_MAX_CONCURRENCY
        assert results.count("429") >= 500 - derivations
        assert "ok" not in results

    @pytest.mark.asyncio
    async def test_cached_success_skips_the_slot(self, manager):
        manager.admin_credential_cached = MagicMock(return_value=True)
        manager.verify_admin_password = MagicMock()
        manager.clear_admin_failures = AsyncMock()
        assert await authenticate_admin_basic("203.0.113.1", "admin", "good") is True
        manager.verify_admin_password.assert_not_called()


class TestInMemoryLockoutFallback:
    @pytest.mark.asyncio
    async def test_failures_age_out_of_the_window(self, manager):
        with patch("core.middleware._admin_lockout.time.time", return_value=1000.0):
            for _ in range(manager._LOCKOUT_MAX_FAILURES - 1):
                await manager.record_admin_failure("203.0.113.5")
        later = 1000.0 + manager._LOCKOUT_WINDOW_SECONDS + 1
        with patch("core.middleware._admin_lockout.time.time", return_value=later):
            await manager.record_admin_failure("203.0.113.5")
            assert manager._lockout_fallback["203.0.113.5"][0] == 1
            await manager.check_admin_lockout("203.0.113.5")  # not locked

    @pytest.mark.asyncio
    async def test_expired_lock_is_not_rearmed_by_one_typo(self, manager):
        with patch("core.middleware._admin_lockout.time.time", return_value=1000.0):
            for _ in range(manager._LOCKOUT_MAX_FAILURES):
                await manager.record_admin_failure("203.0.113.5")
        after = 1000.0 + manager._LOCKOUT_DURATION_SECONDS + 1
        with patch("core.middleware._admin_lockout.time.time", return_value=after):
            await manager.record_admin_failure("203.0.113.5")
            await manager.check_admin_lockout("203.0.113.5")  # must not raise

    @pytest.mark.asyncio
    async def test_map_is_bounded(self, manager):
        manager._LOCKOUT_FALLBACK_MAX_ENTRIES = 50
        for i in range(200):
            await manager.record_admin_failure(f"198.51.100.{i % 250}-{i}")
        assert len(manager._lockout_fallback) <= 51
