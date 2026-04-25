"""
Tests for Tenant Middleware.
"""

import pytest

from core.auth import AuthRole, AuthUser
from core.context import get_current_tenant_id
from core.middleware.tenant import TenantMiddleware


async def _drive_middleware(middleware, scope) -> str:
    captured: dict[str, str] = {}

    async def app(scope, receive, send):
        captured["tenant"] = get_current_tenant_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware.app = app

    async def receive():
        return {"type": "http.request"}

    async def send(_message):
        return None

    await middleware(scope, receive, send)
    return captured["tenant"]


class TestTenantMiddleware:
    @pytest.mark.asyncio
    async def test_tenant_extraction_from_auth_user(self):
        user = AuthUser(user_id="u1", tenant_id="tenant-x", roles={AuthRole.USER})
        middleware = TenantMiddleware(app=lambda *a, **kw: None)  # type: ignore[arg-type]
        scope = {"type": "http", "user": user}
        tenant = await _drive_middleware(middleware, scope)
        assert tenant == "tenant-x"

    @pytest.mark.asyncio
    async def test_default_tenant_if_no_user(self):
        middleware = TenantMiddleware(app=lambda *a, **kw: None)  # type: ignore[arg-type]
        scope = {"type": "http"}
        tenant = await _drive_middleware(middleware, scope)
        assert tenant == "default"
