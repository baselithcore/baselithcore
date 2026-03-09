"""
Unit tests for RouterPlugin.
"""

import pytest
from fastapi import APIRouter

from core.plugins.router_plugin import RouterPlugin
from core.plugins.interface import PluginMetadata
from core.plugins.registry import PluginRegistry


class MockRouterPlugin(RouterPlugin):
    """Mock implementation of RouterPlugin."""

    def __init__(self):
        super().__init__()
        self._router = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(name="test-router-plugin", version="0.1.0")

    async def initialize(self, config):
        await super().initialize(config)

    def create_router(self):
        if not self._router:
            router = APIRouter()
            router.add_api_route("/test", lambda: {"msg": "ok"}, methods=["GET"])
            self._router = router
        return self._router


@pytest.mark.asyncio
async def test_router_plugin_instantiation():
    """Test that RouterPlugin can be instantiated and methods work."""
    plugin = MockRouterPlugin()
    await plugin.initialize({})

    assert plugin.is_initialized()
    assert plugin.get_router_prefix() == "/api/test-router-plugin"
    assert plugin.get_router_tags() == ["test-router-plugin"]


@pytest.mark.asyncio
async def test_get_routers():
    """Test get_routers calls create_router."""
    plugin = MockRouterPlugin()
    await plugin.initialize({})

    routers = plugin.get_routers()
    assert len(routers) == 1
    assert isinstance(routers[0], APIRouter)

    # Verify the route was added
    routes = routers[0].routes
    assert len(routes) == 1
    assert routes[0].path == "/test"


@pytest.mark.asyncio
async def test_registry_registration():
    """Test that registry extracts routers from the plugin."""
    registry = PluginRegistry()
    plugin = MockRouterPlugin()
    await plugin.initialize({})

    registry.register(plugin)

    registered_routers = registry.get_all_routers()
    assert len(registered_routers) == 1
    assert registered_routers[0] == plugin.get_routers()[0]
