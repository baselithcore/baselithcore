import pytest
from unittest.mock import patch
from core.context import (
    get_current_tenant_id,
    set_tenant_context,
    reset_tenant_context,
    TenantContextError,
)


class TestStrictMultiTenancy:
    @pytest.fixture(autouse=True)
    def reset_context(self):
        set_tenant_context("default")
        yield
        set_tenant_context("default")

    def test_default_behavior_relaxed(self):
        """Test that by default (strict=False), missing context returns 'default'."""
        with patch("core.context.get_app_config") as mock_config:
            mock_config.return_value.strict_tenant_isolation = False
            assert get_current_tenant_id() == "default"

    def test_strict_mode_raises_error(self):
        """Test that with strict=True, missing context raises TenantContextError."""
        from core.context import _tenant_context

        with patch("core.context.get_app_config") as mock_config:
            mock_config.return_value.strict_tenant_isolation = True

            # Ensure context is None
            token = _tenant_context.set(None)
            try:
                with pytest.raises(TenantContextError):
                    get_current_tenant_id()
            finally:
                _tenant_context.reset(token)

    def test_strict_mode_with_context(self):
        """Test that with strict=True, correctly set context works."""
        with patch("core.context.get_app_config") as mock_config:
            mock_config.return_value.strict_tenant_isolation = True

            token = set_tenant_context("tenant-123")
            try:
                assert get_current_tenant_id() == "tenant-123"
            finally:
                reset_tenant_context(token)
