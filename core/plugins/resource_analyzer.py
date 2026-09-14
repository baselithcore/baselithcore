"""
Resource analyzer for plugin dependencies and static capabilities.

Analyzes plugin configurations to determine which core services
need to be initialized and extracts plugin capabilities without
importing plugin code at startup.


``PluginDiscovery`` and the static capability scan itself live in
:mod:`core.plugins.capability_scan` (500-line cap); both are re-exported here so
every existing import path keeps working.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger
from core.plugins import _ast_utils

from ._resolve import safe_plugin_path
from .capability_scan import (
    PluginDiscovery,
    get_plugin_source_path,
    parse_plugin_ast,
    scan_plugin_capabilities,
)
from .discovery import find_manifest, merge_plugin_dirs
from .interface import PluginMetadata

logger = get_logger(__name__)


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
        # Memoize discovery keyed by (plugin_dir, manifest_mtime, source_mtime)
        # so unchanged plugins are parsed once even when discover_plugin is
        # called repeatedly (loader.load_plugin, loader.load_all, app_setup).
        self._discovery_cache: dict[
            tuple[str, float, float], PluginDiscovery | None
        ] = {}

    def _get_manifest_path(self, plugin_dir: Path) -> Path | None:
        """Return the preferred manifest path for a plugin directory.

        Delegates to :func:`core.plugins.discovery.find_manifest` so the
        accepted filenames are declared exactly once
        (:data:`~core.plugins.discovery.MANIFEST_FILENAMES`).
        """
        return find_manifest(plugin_dir)

    def _get_plugin_source_path(self, plugin_dir: Path) -> Path | None:
        """Return the plugin module path used for static AST analysis."""
        return get_plugin_source_path(plugin_dir)

    def _parse_plugin_ast(self, plugin_dir: Path) -> ast.Module | None:
        """Parse the plugin source file without importing it."""
        return parse_plugin_ast(plugin_dir)

    # Static AST helpers live in core.plugins._ast_utils (500-line cap);
    # exposed as staticmethods for backward compatibility.
    _base_name = staticmethod(_ast_utils.base_name)
    _get_method_node = staticmethod(_ast_utils.get_method_node)
    _literal_return_value = staticmethod(_ast_utils.literal_return_value)
    _static_eval = staticmethod(_ast_utils.static_eval)
    _dict_return_keys = staticmethod(_ast_utils.dict_return_keys)
    _dict_by_key = staticmethod(_ast_utils.dict_by_key)
    _match_config_key = staticmethod(_ast_utils.match_config_key)

    def _find_plugin_class(self, module_ast: ast.Module) -> ast.ClassDef | None:
        """Find the first class that looks like a plugin implementation."""
        return _ast_utils.find_plugin_class(module_ast)

    def get_plugin_metadata(self, plugin_name: str) -> PluginMetadata | None:
        """
        Load plugin metadata efficiently.

        Tries to use AST parsing first to avoid executing the module.
        Falls back to importing the module if AST parsing fails.

        Args:
            plugin_name: Name of the plugin directory

        Returns:
            PluginMetadata instance or None if failed to load
        """
        try:
            plugin_dir = safe_plugin_path(self.plugins_dir, plugin_name)
        except ValueError as exc:
            logger.warning("Rejected plugin name: %s", exc)
            return None
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

    def discover_plugin(self, plugin_dir: Path) -> PluginDiscovery | None:
        """
        Discover plugin metadata and static capabilities without importing it.

        Results are memoized by ``(plugin_dir, manifest_mtime, source_mtime)``
        so a plugin is read and AST-parsed only once unless its manifest or
        source file changes on disk.

        Args:
            plugin_dir: Path to the plugin directory

        Returns:
            PluginDiscovery or None when discovery fails
        """
        manifest_path = self._get_manifest_path(plugin_dir)
        if manifest_path is None:
            logger.warning("No manifest file found in %s", plugin_dir)
            return None

        source_path = self._get_plugin_source_path(plugin_dir)
        try:
            manifest_mtime = manifest_path.stat().st_mtime
            source_mtime = source_path.stat().st_mtime if source_path else 0.0
        except OSError:
            manifest_mtime = 0.0
            source_mtime = 0.0

        cache_key = (str(plugin_dir), manifest_mtime, source_mtime)
        if cache_key in self._discovery_cache:
            return self._discovery_cache[cache_key]

        discovery = self._discover_plugin_uncached(plugin_dir, manifest_path)
        self._discovery_cache[cache_key] = discovery
        return discovery

    def _discover_plugin_uncached(
        self, plugin_dir: Path, manifest_path: Path
    ) -> PluginDiscovery | None:
        """Parse manifest + plugin AST for ``discover_plugin`` (uncached)."""
        return scan_plugin_capabilities(plugin_dir, manifest_path)

    def discover_plugins(
        self,
        plugin_configs: dict[str, dict[str, Any]],
        extra_dirs: list[Path] | None = None,
    ) -> dict[str, PluginDiscovery]:
        """
        Discover enabled plugins and their static capabilities.

        Args:
            plugin_configs: Dictionary mapping plugin names to config
            extra_dirs: Plugin directories found outside ``plugins_dir`` — in
                practice the ``baselith.plugins`` entry-point group resolved by
                :func:`core.plugins.discovery.iter_entry_point_plugin_dirs`, or
                the already-merged list from
                :meth:`core.plugins.loader.PluginLoader.discover_plugins`.
                Merged in with the directory scan winning on a name clash (and
                warning), so a locally dropped-in tree is never shadowed by an
                installed wheel.

        Returns:
            Mapping of logical plugin name to PluginDiscovery
        """
        discoveries: dict[str, PluginDiscovery] = {}

        candidate_dirs: list[Path] = []
        if self.plugins_dir.exists():
            candidate_dirs = [
                plugin_dir
                for plugin_dir in self.plugins_dir.iterdir()
                if plugin_dir.is_dir() and not plugin_dir.name.startswith((".", "_"))
            ]
        if extra_dirs:
            candidate_dirs = merge_plugin_dirs(candidate_dirs, list(extra_dirs))
        if not candidate_dirs:
            return discoveries

        filter_by_config = len(plugin_configs) > 0

        for plugin_dir in candidate_dirs:
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
        self,
        plugin_configs: dict[str, dict[str, Any]],
        extra_dirs: list[Path] | None = None,
    ) -> dict[str, set[str]]:
        """
        Analyze plugin configurations to determine resource requirements.

        Args:
            plugin_configs: Dictionary mapping plugin names to their configs
                Format: {"plugin_name": {"enabled": True, ...}}
            extra_dirs: Plugin directories outside ``plugins_dir`` — see
                :meth:`discover_plugins`. Without them an entry-point plugin is
                *discovered* but its ``required_resources`` never reach the
                lazy-init order, so it activates against a resource (postgres,
                redis, …) that was never brought up.

        Returns:
            Dictionary with 'required' and 'optional' resource sets:
            {
                "required": {"postgres", "llm"},
                "optional": {"graph", "redis"}
            }
        """
        required_resources: set[str] = set()
        optional_resources: set[str] = set()

        discoveries = self.discover_plugins(plugin_configs, extra_dirs=extra_dirs)

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

    def get_resource_init_order(self, resources: set[str]) -> list[str]:
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

        def visit(resource: str) -> None:
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
    plugins_dir: Path, plugin_configs: dict[str, dict[str, Any]]
) -> dict[str, set[str]]:
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


__all__ = [
    "PluginDiscovery",
    "ResourceAnalyzer",
    "analyze_plugin_resources",
]
