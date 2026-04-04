"""
Unit tests for core.plugins.registry module.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from core.plugins.registry import PluginRegistry
from core.plugins.interface import Plugin


class TestPluginRegistry:
    @pytest.fixture
    def registry(self):
        return PluginRegistry()

    @pytest.fixture
    def mock_plugin(self):
        plugin = MagicMock(spec=Plugin)
        # Setup metadata
        plugin.metadata = MagicMock()
        plugin.metadata.name = "test-plugin"
        plugin.metadata.version = "1.0.0"
        plugin.metadata.dependencies = []

        # Setup async methods
        plugin.initialize = AsyncMock()
        plugin.shutdown = AsyncMock()

        # Setup methods
        plugin.get_agents.return_value = []
        plugin.get_routers.return_value = []
        plugin.get_entity_types.return_value = []
        plugin.get_relationship_types.return_value = []
        plugin.get_intent_patterns.return_value = []
        plugin.get_flow_handlers.return_value = {}
        plugin.get_static_assets_path.return_value = None
        plugin.get_stylesheets.return_value = []
        plugin.get_scripts.return_value = []
        plugin.get_ui_tabs.return_value = []
        plugin.get_mcp_tools.return_value = []

        # Initialization check
        plugin.is_initialized.return_value = True
        plugin.validate_dependencies.return_value = True

        return plugin

    def test_register_success(self, registry, mock_plugin):
        """Test successful plugin registration."""
        registry.register(mock_plugin)
        assert registry.get("test-plugin") == mock_plugin
        assert "test-plugin" in [p.metadata.name for p in registry.get_all()]

    def test_register_duplicate(self, registry, mock_plugin):
        """Test registering duplicate plugin raises ValueError."""
        registry.register(mock_plugin)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(mock_plugin)

    @pytest.mark.asyncio
    async def test_unregister(self, registry, mock_plugin):
        """Test unregistering a plugin."""
        registry.register(mock_plugin)
        await registry.unregister("test-plugin")
        assert registry.get("test-plugin") is None

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self, registry):
        """Test unregistering nonexistent plugin does not raise error."""
        await registry.unregister("nonexistent")  # Should not raise

    def test_get_all_agents(self, registry, mock_plugin):
        """Test retrieving all agents from registered plugins."""
        mock_agent = MagicMock()
        mock_agent.name = "agent1"
        mock_plugin.get_agents.return_value = [mock_agent]

        registry.register(mock_plugin)

        agents = registry.get_all_agents()
        assert "agent1" in agents
        assert agents["agent1"] == mock_agent
        assert registry.get_agent("agent1") == mock_agent

    def test_get_all_routers(self, registry, mock_plugin):
        """Test retrieving all routers."""
        mock_router = MagicMock()
        mock_plugin.get_routers.return_value = [mock_router]

        registry.register(mock_plugin)

        routers = registry.get_all_routers()
        assert mock_router in routers

    def test_frontend_manifest(self, registry, mock_plugin, tmp_path):
        """Test frontend manifest generation."""
        # Create a real temp directory so exists() returns True
        static_dir = tmp_path / "static"
        static_dir.mkdir()

        mock_plugin.get_static_assets_path.return_value = static_dir
        mock_plugin.get_stylesheets.return_value = ["style.css"]
        mock_plugin.get_scripts.return_value = ["script.js"]

        registry.register(mock_plugin)

        manifest = registry.get_frontend_manifest()
        assert "test-plugin" in manifest["plugins"]
        data = manifest["plugins"]["test-plugin"]
        assert data["stylesheets"] == ["style.css"]
        assert data["scripts"] == ["script.js"]

    def test_register_updates_components(self, registry, mock_plugin):
        """Test that registering updates internal component maps."""
        mock_intent = {"name": "intent1", "patterns": []}
        mock_plugin.get_intent_patterns.return_value = [mock_intent]

        registry.register(mock_plugin)

        assert registry.get_intent_pattern("intent1") == mock_intent

    @pytest.mark.asyncio
    async def test_lazy_flow_handler_activates_plugin(self, registry, mock_plugin):
        """Flow handlers should activate cold plugins on first use."""
        activation_calls = []

        class DummyHandler:
            async def handle(self, query, context):
                return {"response": f"handled:{query}", "context": context}

        mock_plugin.is_initialized.return_value = False
        mock_plugin.get_flow_handlers.return_value = {"lazy_intent": DummyHandler()}

        async def _activate(plugin_name: str) -> bool:
            activation_calls.append(plugin_name)
            mock_plugin.is_initialized.return_value = True
            return True

        registry.set_activation_callback(_activate)
        registry.register(mock_plugin, require_initialized=False)

        handler = registry.get_flow_handler("lazy_intent")
        result = await handler.handle("ciao", {"x": 1})

        assert activation_calls == ["test-plugin"]
        assert result["response"] == "handled:ciao"

    def test_concurrent_registration(self, registry):
        """Test thread safety of plugin registration."""
        import threading

        class DummyPlugin:
            def __init__(self, i):
                self.metadata = MagicMock()
                self.metadata.name = f"plugin-{i}"
                self.metadata.version = "1.0.0"
                self.metadata.dependencies = []

            def is_initialized(self):
                return True

            def validate_dependencies(self, available):
                return True

            def get_agents(self):
                return []

            def get_routers(self):
                return []

            def get_entity_types(self):
                return []

            def get_relationship_types(self):
                return []

            def get_intent_patterns(self):
                return []

            def get_flow_handlers(self):
                return {}

            def get_static_assets_path(self):
                return None

            def get_stylesheets(self):
                return []

            def get_scripts(self):
                return []

            def get_ui_tabs(self):
                return []

            def get_mcp_tools(self):
                return []

        def register_worker(i):
            plugin = DummyPlugin(i)
            try:
                registry.register(plugin)
            except ValueError:
                pass  # Should not happen with unique names but guarding

        threads = []
        num_threads = 20
        for i in range(num_threads):
            t = threading.Thread(target=register_worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(registry) == num_threads
        assert len(registry.get_all()) == num_threads
        for i in range(num_threads):
            assert f"plugin-{i}" in registry
