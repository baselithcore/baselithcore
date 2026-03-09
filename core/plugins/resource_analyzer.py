"""
Resource analyzer for plugin dependencies.

Analyzes plugin configurations to determine which core services
need to be initialized.
"""

from core.observability.logging import get_logger
from pathlib import Path
from typing import Dict, Set, Any, Optional
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

        for plugin_name, config in plugin_configs.items():
            # Skip disabled plugins
            if not config.get("enabled", True):
                logger.debug(f"Skipping disabled plugin: {plugin_name}")
                continue

            # Load metadata
            metadata = self.get_plugin_metadata(plugin_name)
            if metadata is None:
                logger.warning(
                    f"Could not determine requirements for {plugin_name}, "
                    "assuming no special resources needed"
                )
                continue

            # Collect resources
            required_resources.update(metadata.required_resources)
            optional_resources.update(metadata.optional_resources)

            logger.debug(
                f"Plugin {plugin_name} requires: {metadata.required_resources}, "
                f"optional: {metadata.optional_resources}"
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
