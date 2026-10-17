from baselith.core.routers.admin import router as core_admin_router
from baselith.core.routers.chat import router as core_chat_router
from baselith.core.routers.tenant import CreateTenantRequest as CoreCreateTenantRequest
from baselith.plugins.api_routers.plugin import ApiRoutersPlugin
from baselith.plugins.api_routers.routes import admin, chat, tenant


def test_legacy_core_router_imports_resolve_to_plugin_exports() -> None:
    assert {route.path for route in core_admin_router.routes} == {
        route.path for route in admin.router.routes
    }
    assert {route.path for route in core_chat_router.routes} == {
        route.path for route in chat.router.routes
    }
    assert CoreCreateTenantRequest is tenant.CreateTenantRequest


def test_api_routers_plugin_exposes_manifest_metadata() -> None:
    plugin = ApiRoutersPlugin()

    assert plugin.metadata.name == "api-routers"
    assert "fastapi" in plugin.metadata.tags
