"""Tests for admin password verification: PBKDF2 iteration floor and the
verified-credential cache that lets identical Basic-auth bursts skip the KDF."""

import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

from core.middleware._admin_credentials import verify_pbkdf2_sha256
from core.middleware.security import SecurityManager


def _encoded(password: str, iterations: int) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest}"


class TestPBKDF2IterationBand:
    """Hashes in the 100k-599k band verify (the floor only rejects below 100k)
    but must warn: the code's own guidance says 600k, and a silent pass hides
    an under-hardened admin credential forever."""

    def test_below_floor_rejected_with_error(self):
        with patch("core.middleware._admin_credentials.logger") as mock_logger:
            assert verify_pbkdf2_sha256(_encoded("pw", 50_000), "pw") is False
        mock_logger.error.assert_called_once()

    def test_band_verifies_but_warns(self):
        with patch("core.middleware._admin_credentials.logger") as mock_logger:
            assert verify_pbkdf2_sha256(_encoded("pw", 100_000), "pw") is True
        mock_logger.warning.assert_called_once()
        assert "600" in str(mock_logger.warning.call_args)

    def test_recommended_iterations_verify_silently(self):
        with patch("core.middleware._admin_credentials.logger") as mock_logger:
            assert verify_pbkdf2_sha256(_encoded("pw", 600_000), "pw") is True
        mock_logger.warning.assert_not_called()
        mock_logger.error.assert_not_called()


class TestVerifiedCredentialCache:
    """Successful PBKDF2 admin verifications are memoized so metric scrapes /
    dashboard polls don't re-run the KDF; wrong passwords are never cached."""

    _ITER = 100_000
    _KDF = "core.middleware.security.verify_pbkdf2_sha256"

    def _encoded_hash(self, password: str) -> str:
        import os

        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, self._ITER
        ).hex()
        return f"pbkdf2_sha256${self._ITER}${salt.hex()}${digest}"

    def _manager(self, mock_security_config, password: str):
        mock_security_config.admin_pass_hashed = MagicMock()
        mock_security_config.admin_pass_hashed.get_secret_value.return_value = (
            self._encoded_hash(password)
        )
        mock_security_config.admin_pass = None
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis_factory.return_value = None
            return SecurityManager(mock_security_config)

    def test_repeated_success_skips_pbkdf2(self, mock_security_config):
        manager = self._manager(mock_security_config, "s3cret-pass")
        with patch(self._KDF, wraps=verify_pbkdf2_sha256) as spy:
            assert manager.verify_admin_password("s3cret-pass") is True
            assert manager.verify_admin_password("s3cret-pass") is True
            assert manager.verify_admin_password("s3cret-pass") is True
        # KDF runs once; the next two identical checks hit the cache.
        assert spy.call_count == 1

    def test_wrong_password_never_cached(self, mock_security_config):
        manager = self._manager(mock_security_config, "s3cret-pass")
        with patch(self._KDF, wraps=verify_pbkdf2_sha256) as spy:
            assert manager.verify_admin_password("wrong") is False
            assert manager.verify_admin_password("wrong") is False
        # Every wrong attempt re-derives — a bad guess can't populate the cache.
        assert spy.call_count == 2
        assert manager._cred_cache.entries == {}

    def test_expired_entry_reverifies(self, mock_security_config):
        manager = self._manager(mock_security_config, "s3cret-pass")
        assert manager.verify_admin_password("s3cret-pass") is True
        # Force the cached entry to be stale.
        token = manager._cred_cache.token("s3cret-pass")
        manager._cred_cache.entries[token] = 0.0
        with patch(self._KDF, wraps=verify_pbkdf2_sha256) as spy:
            assert manager.verify_admin_password("s3cret-pass") is True
        assert spy.call_count == 1


class TestPBKDF2IterationFloor:
    """Under-iterated ADMIN_PASS_HASHED values must be rejected outright."""

    def _manager(self, mock_security_config, encoded: str):
        mock_security_config.admin_pass = None
        mock_security_config.admin_pass_hashed = MagicMock()
        mock_security_config.admin_pass_hashed.get_secret_value.return_value = encoded
        with patch(
            "core.middleware.rate_limiter.create_redis_client"
        ) as mock_redis_factory:
            mock_redis_factory.return_value = AsyncMock()
            return SecurityManager(mock_security_config)

    @staticmethod
    def _encode(password: str, iterations: int) -> str:
        salt = b"\x01" * 16
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"

    def test_low_iteration_hash_rejected_even_with_right_password(
        self, mock_security_config
    ):
        encoded = self._encode("hunter2", 1)
        manager = self._manager(mock_security_config, encoded)
        assert manager.verify_admin_password("hunter2") is False

    def test_conforming_hash_still_verifies(self, mock_security_config):
        encoded = self._encode("hunter2", 600_000)
        manager = self._manager(mock_security_config, encoded)
        assert manager.verify_admin_password("hunter2") is True
        assert manager.verify_admin_password("wrong") is False
