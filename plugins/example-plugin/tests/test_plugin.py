"""Tests for the example plugin.

Run with: ``python -m pytest --import-mode=importlib plugins/example-plugin/tests -q``
(``--import-mode=importlib`` because the plugin directory has a hyphen and an
``__init__.py``, which the default import mode cannot walk).

The plugin package is loaded through ``PluginLoader`` (the directory name
contains a hyphen, so it is not importable with a plain ``import`` statement).
``initialize`` is skipped at load time because the example plugin opens a
PostgreSQL pool in ``initialize()``; the initialization test patches that out.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.plugins import PluginLoader, PluginRegistry

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = PLUGIN_DIR.parent


async def _load_plugin():
    registry = PluginRegistry()
    loader = PluginLoader(PLUGINS_ROOT, registry)
    plugin = await loader.load_plugin(PLUGIN_DIR, initialize=False)
    assert plugin is not None
    return plugin


async def test_example_plugin_metadata():
    plugin = await _load_plugin()

    assert plugin.metadata.name == "example-plugin"
    assert plugin.metadata.version
    assert plugin.metadata.description
    assert plugin.metadata.dependencies == []


async def test_example_plugin_initialization(monkeypatch: pytest.MonkeyPatch):
    plugin = await _load_plugin()
    persistence = __import__(
        f"{type(plugin).__module__.rsplit('.', 1)[0]}.persistence",
        fromlist=["init_pool", "ensure_schema", "close_pool"],
    )
    monkeypatch.setattr(persistence, "init_pool", AsyncMock())
    monkeypatch.setattr(persistence, "ensure_schema", AsyncMock())
    monkeypatch.setattr(persistence, "close_pool", AsyncMock())

    await plugin.initialize({"test_key": "test_value"})
    assert plugin.is_initialized()
    assert plugin.get_config("test_key") == "test_value"

    await plugin.shutdown()
    assert not plugin.is_initialized()


async def test_example_agent_creation():
    plugin = await _load_plugin()

    agent = plugin.create_agent(service=None)

    assert agent.name == "example-agent"
    assert await agent.handle_request("test query") == (
        "Example agent received: test query"
    )


async def test_example_router_creation():
    plugin = await _load_plugin()

    router = plugin.create_router()

    # The plugin-level prefix (``/api/example-plugin`` by default) is applied by
    # the runtime when the router is mounted; the router itself has none.
    assert router.prefix == ""
    assert "example" in router.tags
    assert {route.path for route in router.routes} >= {"/hello", "/config", "/echo"}


async def test_example_entity_and_relationship_types():
    plugin = await _load_plugin()

    entity_types = plugin.register_entity_types()
    rel_types = plugin.register_relationship_types()

    assert {et["type"] for et in entity_types} == {"example_task", "example_note"}
    assert {rt["type"] for rt in rel_types} == {
        "EXAMPLE_DEPENDS_ON",
        "EXAMPLE_RELATES_TO",
    }


async def test_example_intent_patterns_use_name_key():
    plugin = await _load_plugin()

    intents = plugin.get_intent_patterns()

    assert {i["name"] for i in intents} == {"example_hello", "example_help"}
    assert all(i["patterns"] for i in intents)


async def test_example_flow_handlers_follow_runtime_contract():
    plugin = await _load_plugin()

    handlers = plugin.get_flow_handlers()
    greeting = handlers["example_greeting"]
    complex_task = handlers["example_complex"]

    result = await greeting("hi", {"user_name": "Ada"})
    assert result["response"] == "Hello, Ada! I am the Example Flow Handler."

    result = await complex_task("process", {"item_id": 42})
    assert result["data"] == {"processed": True, "id": 42, "query": "process"}


async def test_plugin_registration(monkeypatch: pytest.MonkeyPatch):
    registry = PluginRegistry()
    loader = PluginLoader(PLUGINS_ROOT, registry)
    plugin = await loader.load_plugin(PLUGIN_DIR, initialize=False)
    assert plugin is not None

    # The registry refuses plugins that were not initialized first.
    persistence = __import__(
        f"{type(plugin).__module__.rsplit('.', 1)[0]}.persistence",
        fromlist=["init_pool", "ensure_schema"],
    )
    monkeypatch.setattr(persistence, "init_pool", AsyncMock())
    monkeypatch.setattr(persistence, "ensure_schema", AsyncMock())
    await plugin.initialize({})

    registry.register(plugin)

    assert "example-plugin" in registry
    assert registry.get("example-plugin") is plugin
