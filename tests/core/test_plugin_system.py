"""
Core Plugin System Tests

Tests for the white-label plugin system infrastructure.
"""

import pytest
from core.plugins import Plugin, PluginMetadata, PluginRegistry


class TestPluginMetadata:
    """Test plugin metadata creation and validation."""

    def test_metadata_creation(self):
        """Test creating plugin metadata."""
        metadata = PluginMetadata(
            name="test-plugin",
            version="1.0.0",
            description="Test plugin",
            author="Test Author",
        )
        assert metadata.name == "test-plugin"
        assert metadata.version == "1.0.0"
        assert metadata.description == "Test plugin"
        assert metadata.author == "Test Author"
        assert metadata.dependencies == []

    def test_metadata_with_dependencies(self):
        """Test metadata with dependencies."""
        metadata = PluginMetadata(
            name="test-plugin",
            version="1.0.0",
            description="Test plugin",
            author="Test Author",
            dependencies=["core-plugin", "utils-plugin"],
        )
        assert len(metadata.dependencies) == 2
        assert "core-plugin" in metadata.dependencies


class TestPlugin:
    """Test base plugin functionality."""

    @pytest.mark.asyncio
    async def test_plugin_initialization(self):
        """Test plugin initialization."""

        class TestPlugin(Plugin):
            @property
            def metadata(self):
                return PluginMetadata(
                    name="test", version="1.0.0", description="Test", author="Test"
                )

            async def initialize(self, config):
                self._config = config
                self._initialized = True

            async def shutdown(self):
                self._initialized = False

        plugin = TestPlugin()
        assert not plugin.is_initialized()

        await plugin.initialize({"key": "value"})
        assert plugin.is_initialized()
        assert plugin.get_config("key") == "value"

    @pytest.mark.asyncio
    async def test_plugin_shutdown(self):
        """Test plugin shutdown."""

        class TestPlugin(Plugin):
            @property
            def metadata(self):
                return PluginMetadata(
                    name="test", version="1.0.0", description="Test", author="Test"
                )

            async def initialize(self, config):
                self._initialized = True

            async def shutdown(self):
                self._initialized = False

        plugin = TestPlugin()
        await plugin.initialize({})
        assert plugin.is_initialized()

        await plugin.shutdown()
        assert not plugin.is_initialized()


class TestPluginRegistry:
    """Test plugin registry functionality."""

    @pytest.mark.asyncio
    async def test_registry_register(self):
        """Test registering a plugin."""

        class TestPlugin(Plugin):
            @property
            def metadata(self):
                return PluginMetadata(
                    name="test", version="1.0.0", description="Test", author="Test"
                )

            async def initialize(self, config):
                self._initialized = True

            async def shutdown(self):
                self._initialized = False

        registry = PluginRegistry()
        plugin = TestPlugin()
        await plugin.initialize({})

        registry.register(plugin)
        assert len(registry) == 1
        assert "test" in registry
        assert registry.get("test") == plugin

    @pytest.mark.asyncio
    async def test_registry_unregister(self):
        """Test unregistering a plugin."""

        class TestPlugin(Plugin):
            @property
            def metadata(self):
                return PluginMetadata(
                    name="test", version="1.0.0", description="Test", author="Test"
                )

            async def initialize(self, config):
                self._initialized = True

            async def shutdown(self):
                self._initialized = False

        registry = PluginRegistry()
        plugin = TestPlugin()
        await plugin.initialize({})

        registry.register(plugin)
        assert len(registry) == 1

        await registry.unregister("test")
        assert len(registry) == 0
        assert "test" not in registry


@pytest.mark.asyncio
async def test_plugin_system_integration():
    """Integration test for the complete plugin system."""

    class TestPlugin(Plugin):
        @property
        def metadata(self):
            return PluginMetadata(
                name="integration-test",
                version="1.0.0",
                description="Integration test plugin",
                author="Test",
            )

        async def initialize(self, config):
            self._config = config
            self._initialized = True

        async def shutdown(self):
            self._initialized = False

    # Create registry and plugin
    registry = PluginRegistry()
    plugin = TestPlugin()

    # Initialize and register
    await plugin.initialize({"test_key": "test_value"})
    registry.register(plugin)

    # Verify registration
    assert len(registry) == 1
    retrieved = registry.get("integration-test")
    assert retrieved is not None
    assert retrieved.metadata.name == "integration-test"
    assert retrieved.get_config("test_key") == "test_value"

    # Cleanup
    await registry.unregister("integration-test")
    assert len(registry) == 0
