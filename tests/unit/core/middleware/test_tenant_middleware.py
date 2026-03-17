"""
Tests for Tenant Middleware.
"""

import pytest
from unittest.mock import MagicMock
from core.middleware.tenant import TenantMiddleware
from core.auth import AuthUser, AuthRole
from core.context import get_current_tenant_id


class TestTenantMiddleware:
    @pytest.mark.asyncio
    async def test_tenant_extraction_from_auth_user(self):
        app = MagicMock()
        middleware = TenantMiddleware(app)
        request = MagicMock()

        user = AuthUser(user_id="u1", tenant_id="tenant-x", roles={AuthRole.USER})
        request.state.user = user

        async def call_next(req):
            return get_current_tenant_id()

        tenant = await middleware.dispatch(request, call_next)
        assert tenant == "tenant-x"

    @pytest.mark.asyncio
    async def test_default_tenant_if_no_user(self):
        app = MagicMock()
        middleware = TenantMiddleware(app)
        request = MagicMock()
        # No user attr
        del request.user

        # Also need to mock getattr for state if it defaults to None
        request.state = MagicMock()
        del request.state.user

        async def call_next(req):
            return get_current_tenant_id()

        tenant = await middleware.dispatch(request, call_next)
        assert tenant == "default"
