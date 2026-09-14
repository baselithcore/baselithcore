"""Static plugin capability scanning.

Reads a plugin's manifest and, without importing a line of its code, extracts
what it declares: routes, graph types, intent patterns, flow handlers, UI tabs
and static assets. Importing to find that out would execute third-party code at
startup, before the integrity and signature gates have run.

Split out of ``resource_analyzer.py`` for the 500-line cap. ``PluginDiscovery``
is re-exported there, so ``from core.plugins.resource_analyzer import
PluginDiscovery`` keeps working for every existing caller.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger
from core.plugins import _ast_utils

from .interface import PluginMetadata

logger = get_logger(__name__)


@dataclass(slots=True)
class PluginDiscovery:
    """Static plugin capabilities extracted without importing the module."""

    name: str
    directory_name: str
    plugin_dir: Path
    metadata: PluginMetadata
    provides_routes: bool = False
    router_prefix: str | None = None
    entity_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationship_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    intent_patterns: dict[str, dict[str, Any]] = field(default_factory=dict)
    flow_handler_names: list[str] = field(default_factory=list)
    static_path: Path | None = None
    stylesheets: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    ui_tabs: list[dict[str, str]] = field(default_factory=list)


def get_plugin_source_path(plugin_dir: Path) -> Path | None:
    """Return the plugin module path used for static AST analysis."""
    for filename in ("plugin.py", "__init__.py"):
        source_path = plugin_dir / filename
        if source_path.exists():
            return source_path
    return None


def parse_plugin_ast(plugin_dir: Path) -> ast.Module | None:
    """Parse the plugin source file without importing it."""
    source_path = get_plugin_source_path(plugin_dir)
    if source_path is None:
        return None

    try:
        source = source_path.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(source_path))
    except Exception as exc:
        logger.debug("AST parsing failed for %s: %s", plugin_dir.name, exc)
        return None


def scan_plugin_capabilities(
    plugin_dir: Path, manifest_path: Path
) -> PluginDiscovery | None:
    """Read a plugin's manifest and static capabilities without importing it.

    Args:
        plugin_dir: The plugin directory.
        manifest_path: Its manifest, already located.

    Returns:
        The discovery record, or ``None`` when the manifest does not parse.
        A manifest that is present but invalid returns ``None`` here and is
        refused outright by ``PluginLoader.load_plugin`` — see its comment.
    """
    try:
        metadata = PluginMetadata.from_file(manifest_path)
    except Exception as exc:
        logger.error(
            "Failed to load metadata for plugin %s: %s",
            plugin_dir.name,
            exc,
            exc_info=True,
        )
        return None

    module_ast = parse_plugin_ast(plugin_dir)
    class_node = _ast_utils.find_plugin_class(module_ast) if module_ast else None

    provides_routes = False
    router_prefix: str | None = None
    entity_types: dict[str, dict[str, Any]] = {}
    relationship_types: dict[str, dict[str, Any]] = {}
    intent_patterns: dict[str, dict[str, Any]] = {}
    flow_handler_names: list[str] = []
    stylesheets: list[str] = []
    scripts: list[str] = []
    ui_tabs: list[dict[str, str]] = []

    if class_node is not None:
        base_names = {_ast_utils.base_name(base) for base in class_node.bases}
        provides_routes = (
            "RouterPlugin" in base_names
            or _ast_utils.get_method_node(class_node, "create_router") is not None
            or _ast_utils.get_method_node(class_node, "get_routers") is not None
        )

        router_prefix_value = _ast_utils.literal_return_value(
            class_node, "get_router_prefix"
        )
        if isinstance(router_prefix_value, str):
            router_prefix = router_prefix_value

        entity_items = _ast_utils.literal_return_value(
            class_node, "register_entity_types"
        )
        if entity_items is None:
            entity_items = _ast_utils.literal_return_value(
                class_node, "get_entity_types"
            )
        if isinstance(entity_items, list):
            entity_types = _ast_utils.dict_by_key(entity_items, "type")

        relationship_items = _ast_utils.literal_return_value(
            class_node, "register_relationship_types"
        )
        if relationship_items is None:
            relationship_items = _ast_utils.literal_return_value(
                class_node, "get_relationship_types"
            )
        if isinstance(relationship_items, list):
            relationship_types = _ast_utils.dict_by_key(relationship_items, "type")

        intent_items = _ast_utils.literal_return_value(
            class_node, "get_intent_patterns"
        )
        if isinstance(intent_items, list):
            intent_patterns = _ast_utils.dict_by_key(intent_items, "name")

        flow_handlers = _ast_utils.literal_return_value(class_node, "get_flow_handlers")
        if isinstance(flow_handlers, dict):
            flow_handler_names = [
                intent_name
                for intent_name in flow_handlers.keys()
                if isinstance(intent_name, str)
            ]
        elif not flow_handler_names:
            flow_handler_names = _ast_utils.dict_return_keys(
                class_node, "get_flow_handlers"
            )

        stylesheets_value = _ast_utils.literal_return_value(
            class_node, "get_stylesheets"
        )
        if isinstance(stylesheets_value, list):
            stylesheets = [item for item in stylesheets_value if isinstance(item, str)]

        scripts_value = _ast_utils.literal_return_value(class_node, "get_scripts")
        if isinstance(scripts_value, list):
            scripts = [item for item in scripts_value if isinstance(item, str)]

        ui_tabs_value = _ast_utils.literal_return_value(class_node, "get_ui_tabs")
        if isinstance(ui_tabs_value, list):
            ui_tabs = [item for item in ui_tabs_value if isinstance(item, dict)]

    if router_prefix is None and provides_routes:
        router_prefix = f"/api/{metadata.name}"

    static_dir = plugin_dir / "static"
    static_path = static_dir if static_dir.exists() else None

    return PluginDiscovery(
        name=metadata.name,
        directory_name=plugin_dir.name,
        plugin_dir=plugin_dir,
        metadata=metadata,
        provides_routes=provides_routes,
        router_prefix=router_prefix,
        entity_types=entity_types,
        relationship_types=relationship_types,
        intent_patterns=intent_patterns,
        flow_handler_names=flow_handler_names,
        static_path=static_path,
        stylesheets=stylesheets,
        scripts=scripts,
        ui_tabs=ui_tabs,
    )


__all__ = [
    "PluginDiscovery",
    "get_plugin_source_path",
    "parse_plugin_ast",
    "scan_plugin_capabilities",
]
