"""Tests for per-plugin tenancy (1 user = 1 tenant) support.

Covers the user identity context var, the ``resolve_plugin_tenant`` resolver,
the ``PluginMetadata.tenancy`` field, ``Plugin.tenant_key`` and the binding of
the user context at the tenant/auth chokepoints.
"""

import pytest

from core.auth import AuthRole, AuthUser
from core.context import (
    get_current_tenant_id,
    get_current_user_id,
    reset_tenant_context,
    reset_user_context,
    resolve_plugin_tenant,
    set_tenant_context,
    set_user_context,
)
from core.middleware.tenant import TenantMiddleware
from core.plugins.interface import Plugin, PluginMetadata


class TestUserContext:
    def test_default_user_is_none(self):
        assert get_current_user_id() is None

    def test_set_reset(self):
        token = set_user_context("user-123")
        try:
            assert get_current_user_id() == "user-123"
        finally:
            reset_user_context(token)
        assert get_current_user_id() is None

    async def test_async_propagation(self):
        token = set_user_context("user-async")
        try:

            async def inner() -> str | None:
                return get_current_user_id()

            assert await inner() == "user-async"
        finally:
            reset_user_context(token)


class TestResolvePluginTenant:
    def test_personal_uses_user_id_over_session_tenant(self):
        tt = set_tenant_context("org-shared")
        ut = set_user_context("alice")
        try:
            assert resolve_plugin_tenant("personal") == "alice"
            assert resolve_plugin_tenant("shared") == "org-shared"
        finally:
            reset_user_context(ut)
            reset_tenant_context(tt)

    def test_personal_falls_back_to_tenant_when_no_user(self):
        # No user bound (background task / script): degrade to the session
        # tenant rather than raising or returning an empty key.
        tt = set_tenant_context("org-shared")
        try:
            assert resolve_plugin_tenant("personal") == "org-shared"
        finally:
            reset_tenant_context(tt)

    def test_unknown_mode_behaves_as_shared(self):
        tt = set_tenant_context("org-shared")
        ut = set_user_context("alice")
        try:
            assert resolve_plugin_tenant("bogus") == "org-shared"
        finally:
            reset_user_context(ut)
            reset_tenant_context(tt)

    def test_personal_defaults_when_nothing_bound(self):
        assert resolve_plugin_tenant("personal") == "default"


class TestPluginMetadataTenancy:
    def test_default_is_shared(self):
        md = PluginMetadata(name="x", version="1.0.0")
        assert md.tenancy == "shared"
        assert md.to_dict()["tenancy"] == "shared"

    def test_personal_preserved(self):
        md = PluginMetadata(name="x", version="1.0.0", tenancy="personal")
        assert md.tenancy == "personal"

    def test_unknown_normalized_to_shared(self):
        md = PluginMetadata(name="x", version="1.0.0", tenancy="weird")
        assert md.tenancy == "shared"

    def test_from_file_parses_tenancy(self, tmp_path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("name: p\nversion: 1.0.0\ntenancy: personal\n")
        md = PluginMetadata.from_file(manifest)
        assert md.tenancy == "personal"

    def test_from_file_defaults_shared(self, tmp_path):
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text("name: p\nversion: 1.0.0\n")
        md = PluginMetadata.from_file(manifest)
        assert md.tenancy == "shared"


class _StubPlugin(Plugin):
    """Minimal plugin that bypasses manifest file lookup for tenant_key tests."""

    def __init__(self, tenancy: str):
        super().__init__()
        # cached_property is a non-data descriptor, so an instance attribute of
        # the same name shadows it — metadata is never read from disk here.
        self.metadata = PluginMetadata(  # type: ignore[misc]
            name="stub", version="1.0.0", tenancy=tenancy
        )


class TestPluginTenantKey:
    def test_personal_plugin_scopes_by_user(self):
        tt = set_tenant_context("org-shared")
        ut = set_user_context("bob")
        try:
            assert _StubPlugin("personal").tenant_key() == "bob"
            assert _StubPlugin("shared").tenant_key() == "org-shared"
        finally:
            reset_user_context(ut)
            reset_tenant_context(tt)


class TestTenantMiddlewareBindsUser:
    @pytest.mark.asyncio
    async def test_middleware_binds_user_context(self):
        captured: dict[str, str | None] = {}

        async def downstream(scope, receive, send):
            captured["tenant"] = get_current_tenant_id()
            captured["user"] = get_current_user_id()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        user = AuthUser(user_id="u1", tenant_id="t1", roles={AuthRole.USER})
        middleware = TenantMiddleware(downstream)
        scope = {"type": "http", "user": user}

        async def receive():
            return {"type": "http.request"}

        async def send(_message):
            return None

        await middleware(scope, receive, send)
        assert captured["tenant"] == "t1"
        assert captured["user"] == "u1"
        # Context restored after the request.
        assert get_current_user_id() is None
