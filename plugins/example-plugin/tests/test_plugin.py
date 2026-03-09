"""Tests for the example plugin."""

from pathlib import Path

from core.plugins import PluginRegistry, PluginLoader
from plugins.example_plugin.plugin import ExamplePlugin, ExampleAgent


def test_example_plugin_metadata():
    """Test plugin metadata."""
    plugin = ExamplePlugin()

    assert plugin.metadata.name == "example-plugin"
    assert plugin.metadata.version == "0.1.0"
    assert plugin.metadata.description
    assert plugin.metadata.dependencies == []


def test_example_plugin_initialization():
    """Test plugin initialization."""
    plugin = ExamplePlugin()
    config = {"test_key": "test_value"}

    plugin.initialize(config)

    assert plugin.is_initialized()
    assert plugin.get_config("test_key") == "test_value"


def test_example_agent_creation():
    """Test agent creation."""
    plugin = ExamplePlugin()
    plugin.initialize({})

    agent = plugin.create_agent(service=None)

    assert isinstance(agent, ExampleAgent)
    assert agent.name == "example-agent"


def test_example_agent_handle_request():
    """Test agent request handling."""
    agent = ExampleAgent()

    response = agent.handle_request("test query")

    assert "test query" in response
    assert "Example agent received" in response


def test_example_router_creation():
    """Test router creation."""
    plugin = ExamplePlugin()
    plugin.initialize({})

    router = plugin.create_router()

    assert router.prefix == "/api/example"
    assert "example" in router.tags


def test_example_entity_types():
    """Test entity type registration."""
    plugin = ExamplePlugin()
    plugin.initialize({})

    entity_types = plugin.register_entity_types()

    assert len(entity_types) == 2
    assert any(et["type"] == "example_task" for et in entity_types)
    assert any(et["type"] == "example_note" for et in entity_types)


def test_example_relationship_types():
    """Test relationship type registration."""
    plugin = ExamplePlugin()
    plugin.initialize({})

    rel_types = plugin.register_relationship_types()

    assert len(rel_types) == 2
    assert any(rt["type"] == "EXAMPLE_DEPENDS_ON" for rt in rel_types)
    assert any(rt["type"] == "EXAMPLE_RELATES_TO" for rt in rel_types)


def test_example_intent_patterns():
    """Test intent pattern registration."""
    plugin = ExamplePlugin()
    plugin.initialize({})

    intents = plugin.get_intent_patterns()

    assert len(intents) == 2
    assert any(i["name"] == "example_hello" for i in intents)
    assert any(i["name"] == "example_help" for i in intents)


def test_plugin_loading():
    """Test plugin loading through loader."""
    registry = PluginRegistry()
    loader = PluginLoader(Path("plugins/"), registry)

    # Load the example plugin
    plugin_dir = Path("plugins/example-plugin")
    if plugin_dir.exists():
        plugin = loader.load_plugin(plugin_dir, {"test": "config"})

        assert plugin is not None
        assert plugin.metadata.name == "example-plugin"
        assert plugin.is_initialized()


def test_plugin_registration():
    """Test plugin registration in registry."""
    registry = PluginRegistry()
    plugin = ExamplePlugin()
    plugin.initialize({})

    registry.register(plugin)

    assert len(registry) == 1
    assert "example-plugin" in registry
    assert registry.get("example-plugin") == plugin


def test_plugin_shutdown():
    """Test plugin shutdown."""
    plugin = ExamplePlugin()
    plugin.initialize({})

    assert plugin.is_initialized()

    plugin.shutdown()

    assert not plugin.is_initialized()
