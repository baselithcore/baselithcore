"""
Resource analyzer for plugin dependencies and static capabilities.

Analyzes plugin configurations to determine which core services
need to be initialized and extracts plugin capabilities without
importing plugin code at startup.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Set

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
    router_prefix: Optional[str] = None
    entity_types: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    relationship_types: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    intent_patterns: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    flow_handler_names: list[str] = field(default_factory=list)
    static_path: Optional[Path] = None
    stylesheets: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    ui_tabs: list[Dict[str, str]] = field(default_factory=list)


class ResourceAnalyzer:
    """
    Analyzes plugin requirements to determine which core resources to load.

    This analyzer scans plugin configurations and metadata to build a
    dependency graph of required core services.
    """

    # Resource dependencies (what must be initialized before what)
    DEFAULT_DEPENDENCIES = {
        "redis": [],  # No deps
        "postgres": [],  # No deps
        "graph": ["redis"],  # Graph uses Redis
        "vectorstore": ["postgres"],  # Qdrant may use postgres for metadata
        "memory": ["vectorstore", "redis"],  # Memory uses both
        "llm": [],  # No deps
        "evaluation": ["memory", "llm"],  # Evaluation needs memory and LLM
        "evolution": ["memory", "evaluation"],  # Evolution builds on evaluation
    }

    def __init__(self, plugins_dir: Path):
        """
        Initialize resource analyzer.

        Args:
            plugins_dir: Directory containing plugin packages
        """
        self.plugins_dir = Path(plugins_dir)

    def _get_manifest_path(self, plugin_dir: Path) -> Optional[Path]:
        """Return the preferred manifest path for a plugin directory."""
        for filename in ("manifest.yaml", "manifest.yml", "manifest.json"):
            manifest_path = plugin_dir / filename
            if manifest_path.exists():
                return manifest_path
        return None

    def _get_plugin_source_path(self, plugin_dir: Path) -> Optional[Path]:
        """Return the plugin module path used for static AST analysis."""
        for filename in ("plugin.py", "__init__.py"):
            source_path = plugin_dir / filename
            if source_path.exists():
                return source_path
        return None

    def _parse_plugin_ast(self, plugin_dir: Path) -> Optional[ast.Module]:
        """Parse the plugin source file without importing it."""
        source_path = self._get_plugin_source_path(plugin_dir)
        if source_path is None:
            return None

        try:
            source = source_path.read_text(encoding="utf-8")
            return ast.parse(source, filename=str(source_path))
        except Exception as exc:
            logger.debug("AST parsing failed for %s: %s", plugin_dir.name, exc)
            return None

    # Static AST helpers live in core.plugins._ast_utils (500-line cap);
    # exposed as staticmethods for backward compatibility.
    _base_name = staticmethod(_ast_utils.base_name)
    _get_method_node = staticmethod(_ast_utils.get_method_node)
    _literal_return_value = staticmethod(_ast_utils.literal_return_value)
    _static_eval = staticmethod(_ast_utils.static_eval)
    _dict_return_keys = staticmethod(_ast_utils.dict_return_keys)
    _dict_by_key = staticmethod(_ast_utils.dict_by_key)
    _match_config_key = staticmethod(_ast_utils.match_config_key)

    def _find_plugin_class(self, module_ast: ast.Module) -> Optional[ast.ClassDef]:
        """Find the first class that looks like a plugin implementation."""
        return _ast_utils.find_plugin_class(module_ast)

    def get_plugin_metadata(self, plugin_name: str) -> Optional[PluginMetadata]:
        """
        Load plugin metadata efficiently.

        Tries to use AST parsing first to avoid executing the module.
        Falls back to importing the module if AST parsing fails.

        Args:
            plugin_name: Name of the plugin directory

        Returns:
            PluginMetadata instance or None if failed to load
        """
        plugin_dir = self.plugins_dir / plugin_name
        if not plugin_dir.exists():
            logger.warning(f"Plugin directory not found: {plugin_dir}")
            return None

        # Look for manifest.yaml first (preferred), then manifest.yml, then manifest.json
        manifest_yaml_path = plugin_dir / "manifest.yaml"
        manifest_yml_path = plugin_dir / "manifest.yml"
        manifest_json_path = plugin_dir / "manifest.json"

        try:
            if manifest_yaml_path.exists():
                return PluginMetadata.from_file(manifest_yaml_path)
            elif manifest_yml_path.exists():
                return PluginMetadata.from_file(manifest_yml_path)
            elif manifest_json_path.exists():
                return PluginMetadata.from_file(manifest_json_path)
        except Exception as e:
            logger.error(
                f"Failed to load metadata for plugin {plugin_name}: {e}", exc_info=True
            )
            return None

        logger.warning(f"No manifest file found in {plugin_dir}")
        return None

    def discover_plugin(self, plugin_dir: Path) -> Optional[PluginDiscovery]:
        """
        Discover plugin metadata and static capabilities without importing it.

        Args:
            plugin_dir: Path to the plugin directory

        Returns:
            PluginDiscovery or None when discovery fails
        """
        manifest_path = self._get_manifest_path(plugin_dir)
        if manifest_path is None:
            logger.warning("No manifest file found in %s", plugin_dir)
            return None

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

        module_ast = self._parse_plugin_ast(plugin_dir)
        class_node = self._find_plugin_class(module_ast) if module_ast else None

        provides_routes = False
        router_prefix: Optional[str] = None
        entity_types: Dict[str, Dict[str, Any]] = {}
        relationship_types: Dict[str, Dict[str, Any]] = {}
        intent_patterns: Dict[str, Dict[str, Any]] = {}
        flow_handler_names: list[str] = []
        stylesheets: list[str] = []
        scripts: list[str] = []
        ui_tabs: list[Dict[str, str]] = []

        if class_node is not None:
            base_names = {self._base_name(base) for base in class_node.bases}
            provides_routes = (
                "RouterPlugin" in base_names
                or self._get_method_node(class_node, "create_router") is not None
                or self._get_method_node(class_node, "get_routers") is not None
            )

            router_prefix_value = self._literal_return_value(
                class_node, "get_router_prefix"
            )
            if isinstance(router_prefix_value, str):
                router_prefix = router_prefix_value

            entity_items = self._literal_return_value(
                class_node, "register_entity_types"
            )
            if entity_items is None:
                entity_items = self._literal_return_value(
                    class_node, "get_entity_types"
                )
            if isinstance(entity_items, list):
                entity_types = self._dict_by_key(entity_items, "type")

            relationship_items = self._literal_return_value(
                class_node, "register_relationship_types"
            )
            if relationship_items is None:
                relationship_items = self._literal_return_value(
                    class_node, "get_relationship_types"
                )
            if isinstance(relationship_items, list):
                relationship_types = self._dict_by_key(relationship_items, "type")

            intent_items = self._literal_return_value(class_node, "get_intent_patterns")
            if isinstance(intent_items, list):
                intent_patterns = self._dict_by_key(intent_items, "name")

            flow_handlers = self._literal_return_value(class_node, "get_flow_handlers")
            if isinstance(flow_handlers, dict):
                flow_handler_names = [
                    intent_name
                    for intent_name in flow_handlers.keys()
                    if isinstance(intent_name, str)
                ]
            elif not flow_handler_names:
                flow_handler_names = self._dict_return_keys(
                    class_node, "get_flow_handlers"
                )

            stylesheets_value = self._literal_return_value(
                class_node, "get_stylesheets"
            )
            if isinstance(stylesheets_value, list):
                stylesheets = [
                    item for item in stylesheets_value if isinstance(item, str)
                ]

            scripts_value = self._literal_return_value(class_node, "get_scripts")
            if isinstance(scripts_value, list):
                scripts = [item for item in scripts_value if isinstance(item, str)]

            ui_tabs_value = self._literal_return_value(class_node, "get_ui_tabs")
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

    def discover_plugins(
        self, plugin_configs: Dict[str, Dict[str, Any]]
    ) -> Dict[str, PluginDiscovery]:
        """
        Discover enabled plugins and their static capabilities.

        Args:
            plugin_configs: Dictionary mapping plugin names to config

        Returns:
            Mapping of logical plugin name to PluginDiscovery
        """
        discoveries: Dict[str, PluginDiscovery] = {}

        if not self.plugins_dir.exists():
            return discoveries

        filter_by_config = len(plugin_configs) > 0

        for plugin_dir in self.plugins_dir.iterdir():
            if not plugin_dir.is_dir() or plugin_dir.name.startswith((".", "_")):
                continue

            discovery = self.discover_plugin(plugin_dir)
            if discovery is None:
                continue

            config_key = self._match_config_key(
                plugin_configs, discovery.directory_name, discovery.name
            )

            if filter_by_config and config_key is None:
                logger.debug(
                    "Skipping plugin %s (not present in config)",
                    discovery.directory_name,
                )
                continue

            plugin_config = plugin_configs.get(config_key or discovery.name, {})
            if not plugin_config.get("enabled", True):
                logger.debug("Skipping disabled plugin: %s", discovery.name)
                continue

            discoveries[discovery.name] = discovery

        return discoveries

    def analyze_requirements(
        self, plugin_configs: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Set[str]]:
        """
        Analyze plugin configurations to determine resource requirements.

        Args:
            plugin_configs: Dictionary mapping plugin names to their configs
                Format: {"plugin_name": {"enabled": True, ...}}

        Returns:
            Dictionary with 'required' and 'optional' resource sets:
            {
                "required": {"postgres", "llm"},
                "optional": {"graph", "redis"}
            }
        """
        required_resources: Set[str] = set()
        optional_resources: Set[str] = set()

        discoveries = self.discover_plugins(plugin_configs)

        for plugin_name, discovery in discoveries.items():
            metadata = discovery.metadata
            required_resources.update(metadata.required_resources)
            optional_resources.update(metadata.optional_resources)

            logger.debug(
                "Plugin %s requires: %s, optional: %s",
                plugin_name,
                metadata.required_resources,
                metadata.optional_resources,
            )

        # Remove optional resources that are already required
        optional_resources -= required_resources

        logger.info(
            f"📊 Resource analysis complete: "
            f"{len(required_resources)} required, "
            f"{len(optional_resources)} optional"
        )
        logger.info(f"   Required: {sorted(required_resources)}")
        if optional_resources:
            logger.info(f"   Optional: {sorted(optional_resources)}")

        return {
            "required": required_resources,
            "optional": optional_resources,
        }

    def get_resource_init_order(self, resources: Set[str]) -> list[str]:
        """
        Determine initialization order for resources based on dependencies.

        Args:
            resources: Set of resource names to initialize

        Returns:
            List of resource names in initialization order
        """
        dependencies = self.DEFAULT_DEPENDENCIES

        # Topological sort
        ordered = []
        visited = set()
        visiting = set()

        def visit(resource: str):
            """
            Recursively visit resources resolving dependencies.

            Args:
                resource: The name of the resource to process.
            """
            if resource in visited:
                return
            if resource in visiting:
                raise ValueError(
                    f"Circular dependency detected for resource: {resource}"
                )

            visiting.add(resource)

            # Visit dependencies first
            for dep in dependencies.get(resource, []):
                if dep in resources:  # Only visit if this dep is also needed
                    visit(dep)

            visiting.remove(resource)
            visited.add(resource)
            ordered.append(resource)

        # Visit all resources
        for resource in resources:
            if resource not in visited:
                visit(resource)

        return ordered


def analyze_plugin_resources(
    plugins_dir: Path, plugin_configs: Dict[str, Dict[str, Any]]
) -> Dict[str, Set[str]]:
    """
    Convenience function to analyze plugin resource requirements.

    Args:
        plugins_dir: Directory containing plugin packages
        plugin_configs: Dictionary mapping plugin names to their configs

    Returns:
        Dictionary with 'required' and 'optional' resource sets
    """
    analyzer = ResourceAnalyzer(plugins_dir)
    return analyzer.analyze_requirements(plugin_configs)
