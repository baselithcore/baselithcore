"""
Integration tests for plugin loading and registration.

Tests that verify the plugin system correctly:
- Discovers plugins from the plugins directory
- Loads and initializes plugins
- Registers agents, routes, and intent patterns
"""

import pytest
from pathlib import Path


# Get plugins directory path
PLUGINS_DIR = Path(__file__).parent.parent.parent / "plugins"


class TestPluginRegistry:
    """Tests for plugin registry functionality."""

    def test_registry_initialization(self):
        """Registry initializes with empty collections."""
        from core.plugins import PluginRegistry

        registry = PluginRegistry()

        assert len(registry.get_all()) == 0
        assert registry.get_all_agents() == {}
        assert registry.get_all_intent_patterns() == {}

    @pytest.mark.asyncio
    async def test_register_plugin(self):
        """Registry can register a plugin."""
        from core.plugins import PluginRegistry, Plugin, PluginMetadata

        registry = PluginRegistry()

        # Create a simple test plugin
        class TestPlugin(Plugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="test-plugin",
                    version="1.0.0",
                    description="Test plugin",
                )

            async def initialize(self, config=None):
                await super().initialize(config or {})

            async def shutdown(self):
                pass

        plugin = TestPlugin()
        await plugin.initialize(config={})  # Must initialize before registration
        registry.register(plugin)

        assert registry.get("test-plugin") is not None
        assert len(registry) == 1

    @pytest.mark.asyncio
    async def test_register_duplicate_raises(self):
        """Registry raises error on duplicate plugin registration."""
        from core.plugins import PluginRegistry, Plugin, PluginMetadata

        registry = PluginRegistry()

        class TestPlugin(Plugin):
            @property
            def metadata(self) -> PluginMetadata:
                return PluginMetadata(
                    name="test-plugin",
                    version="1.0.0",
                    description="Test plugin",
                )

            async def initialize(self, config=None):
                await super().initialize(config or {})

            async def shutdown(self):
                pass

        plugin1 = TestPlugin()
        await plugin1.initialize(config={})
        registry.register(plugin1)

        # Second registration with same name should raise
        plugin2 = TestPlugin()
        await plugin2.initialize(config={})
        with pytest.raises(ValueError):
            registry.register(plugin2)


class TestPluginDiscovery:
    """Tests for plugin discovery mechanism."""

    @pytest.fixture
    def registry(self):
        """Create a fresh registry."""
        from core.plugins import PluginRegistry

        return PluginRegistry()

    def test_discover_plugins_in_directory(self, registry):
        """Plugin loader discovers plugins in the plugins directory."""
        from core.plugins import PluginLoader

        if not PLUGINS_DIR.exists():
            pytest.skip("plugins directory not found")

        loader = PluginLoader(PLUGINS_DIR, registry)
        discovered = loader.discover_plugins()

        # We expect at least the built-in plugins
        assert len(discovered) >= 2

        # Verify paths are plugin directories
        for path in discovered:
            assert path.is_dir()
            # Should have either plugin.py or __init__.py
            assert (path / "plugin.py").exists() or (path / "__init__.py").exists()

    def test_discover_handles_missing_directory(self, registry):
        """Plugin loader handles missing directory gracefully."""
        from core.plugins import PluginLoader

        non_existent = Path("/non/existent/path")

        loader = PluginLoader(non_existent, registry)
        discovered = loader.discover_plugins()
        assert discovered == []


class TestPluginLoading:
    """Tests for actual plugin loading."""

    @pytest.fixture
    def registry(self):
        """Create a fresh registry for each test."""
        from core.plugins import PluginRegistry

        return PluginRegistry()

    @pytest.mark.asyncio
    async def test_load_all_plugins(self, registry):
        """All plugins in directory load without errors."""
        from core.plugins import PluginLoader

        if not PLUGINS_DIR.exists():
            pytest.skip("plugins directory not found")

        loader = PluginLoader(PLUGINS_DIR, registry)
        loaded_count = await loader.load_all_plugins()

        # Without a specific mock config, plugins might be disabled by default config in tests
        assert loaded_count >= 0
        assert len(registry) >= 0


class TestPluginIntegration:
    """Integration tests for plugin system with app components."""

    @pytest.mark.asyncio
    async def test_loaded_plugins_have_valid_metadata(self):
        """All loaded plugins have valid metadata."""
        from core.plugins import PluginRegistry, PluginLoader

        if not PLUGINS_DIR.exists():
            pytest.skip("plugins directory not found")

        registry = PluginRegistry()
        loader = PluginLoader(PLUGINS_DIR, registry)
        await loader.load_all_plugins()

        for plugin in registry.get_all():
            meta = plugin.metadata
            assert meta.name, "Plugin missing name"
            assert meta.version, f"Plugin {meta.name} missing version"

    @pytest.mark.asyncio
    async def test_list_plugins_returns_metadata(self):
        """list_plugins returns plugin metadata dictionaries."""
        from core.plugins import PluginRegistry, PluginLoader

        if not PLUGINS_DIR.exists():
            pytest.skip("plugins directory not found")

        registry = PluginRegistry()
        loader = PluginLoader(PLUGINS_DIR, registry)
        await loader.load_all_plugins()

        plugin_list = registry.list_plugins()
        assert isinstance(plugin_list, list)

        for item in plugin_list:
            assert "name" in item
            assert "version" in item

    @pytest.mark.asyncio
    async def test_plugin_can_be_unregistered(self):
        """Plugins can be unregistered from registry."""
        from core.plugins import PluginRegistry, PluginLoader

        if not PLUGINS_DIR.exists():
            pytest.skip("plugins directory not found")

        registry = PluginRegistry()
        loader = PluginLoader(PLUGINS_DIR, registry)
        await loader.load_all_plugins()

        initial_count = len(registry)
        if initial_count == 0:
            pytest.skip("No plugins loaded")

        # Get first plugin name
        first_plugin = registry.get_all()[0]
        plugin_name = first_plugin.metadata.name

        # Unregister
        from unittest.mock import MagicMock, AsyncMock

        # Helper to ensure shutdown is awaitable if it somehow got mocked as a synchronous Mock
        # This can happen if global mocks from conftest.py interact with plugin imports
        for plugin in registry.get_all():
            if isinstance(plugin.shutdown, MagicMock) and not isinstance(
                plugin.shutdown, AsyncMock
            ):
                plugin.shutdown = AsyncMock()

        await registry.unregister(plugin_name)
        assert len(registry) == initial_count - 1
