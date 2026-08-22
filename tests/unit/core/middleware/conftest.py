"""Shared fixtures for core middleware unit tests."""

from unittest.mock import MagicMock

import pytest

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
    config.auth_failure_limit_per_minute = 20
    config.security_headers_enabled = True
    config.frame_options = "DENY"
    config.content_security_policy = "default-src 'self'"
    config.permissions_policy = None
    config.enable_hsts = False
    config.hsts_max_age = 31536000
    return config
