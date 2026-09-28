"""Plugins contribute connectors through ``Plugin.get_connectors()``."""

from __future__ import annotations

import pytest

from core.connectors import (
    BaseConnector,
    ConnectorSpec,
    get_connector_registry,
    reset_connector_registry,
)
from core.plugins import Plugin
from core.plugins._metadata import PluginMetadata
from core.plugins.registry import PluginRegistry


class Crm(BaseConnector):
    spec = ConnectorSpec(name="hook_crm", display_name="CRM")


def _plugin(name: str, connectors: list) -> Plugin:
    class _P(Plugin):
        metadata = PluginMetadata(name=name, version="1.0.0")  # type: ignore[assignment]

        def get_connectors(self) -> list:
            return connectors

    return _P()


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_connector_registry()
    yield
    reset_connector_registry()


def test_default_plugin_contributes_no_connectors():
    class Plain(Plugin):
        metadata = PluginMetadata(name="plain", version="1.0.0")  # type: ignore[assignment]

    assert Plain().get_connectors() == []
    PluginRegistry().register_all_components(Plain())
    assert get_connector_registry().names() == []


def test_connectors_are_registered_with_the_plugin_as_owner():
    PluginRegistry().register_all_components(_plugin("crm_plugin", [Crm]))
    entry = get_connector_registry().require("hook_crm")
    assert entry.owner == "crm_plugin"


def test_cleanup_removes_the_plugin_connectors():
    registry = PluginRegistry()
    registry.register_all_components(_plugin("crm_plugin", [Crm]))
    registry._cleanup_plugin_components("crm_plugin")
    assert "hook_crm" not in get_connector_registry()


def test_a_colliding_or_malformed_connector_never_blocks_the_plugin():
    registry = PluginRegistry()
    registry.register_all_components(_plugin("first", [Crm]))
    registry.register_all_components(_plugin("second", [Crm, object()]))
    assert get_connector_registry().require("hook_crm").owner == "first"


def test_a_raising_hook_never_blocks_the_plugin():
    class Boom(Plugin):
        metadata = PluginMetadata(name="boom", version="1.0.0")  # type: ignore[assignment]

        def get_connectors(self) -> list:
            raise RuntimeError("broken")

    PluginRegistry().register_all_components(Boom())
    assert get_connector_registry().names() == []


def test_a_non_iterable_hook_result_never_blocks_the_plugin():
    from unittest.mock import Mock

    plugin = Mock()
    plugin.metadata.name = "mocked"
    PluginRegistry()._register_connectors(plugin)
    assert get_connector_registry().names() == []
