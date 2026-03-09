"""
Base plugin interface for the BaselithCore framework.

This module defines the contract that all plugins must satisfy to be
integrated into the core system. It provides:
1. `PluginMetadata`: A structured containter for plugin identity and requirements.
2. `Plugin`: The abstract base class providing hooks for agents, UI, and APIs.
"""

from abc import ABC
from typing import Any, Dict, List, Optional
from pathlib import Path
from core.observability.logging import get_logger

logger = get_logger(__name__)


class PluginMetadata:
    """
    Identity and dependency container for a plugin.

    This class encapsulates all static information about a plugin,
    including its versioning constraints, required core resources,
    and external Python dependencies.
    """

    def __init__(
        self,
        name: str,
        version: str,
        description: str = "",
        author: str = "",
        dependencies: Optional[List[str]] = None,
        required_resources: Optional[List[str]] = None,
        optional_resources: Optional[List[str]] = None,
        python_dependencies: Optional[List[str]] = None,
        plugin_dependencies: Optional[Dict[str, str]] = None,
        min_core_version: Optional[str] = None,
        max_core_version: Optional[str] = None,
        homepage: str = "",
        license: str = "",
        tags: Optional[List[str]] = None,
        icon: str = "",
        screenshots: Optional[List[str]] = None,
        category: str = "Generic",
        environment_variables: Optional[List[str]] = None,
        readiness: str = "stable",
    ):
        """
        Initialize plugin metadata.

        Args:
            name: Unique identifier for the plugin (e.g., "auth-provider").
            version: Semantic version string (e.g., "1.0.0").
            description: Short summary of the plugin's purpose.
            author: Name or entity responsible for the plugin.
            dependencies: [Legacy] List of required plugin names.
            required_resources: Core system components needed (e.g., ["postgres", "llm"]).
            optional_resources: Components that enhance the plugin if present.
            python_dependencies: Pip-installable packages required (e.g., ["requests>=2.0.0"]).
            plugin_dependencies: Map of plugin names to version constraints.
            min_core_version: Minimum compatible framework version.
            max_core_version: Maximum compatible framework version.
            homepage: URL to documentation or source code.
            license: SPDX license identifier (e.g., "MIT").
            tags: Keywords for categorization in the UI or registry.
            icon: Relative path to icon image or URL.
            screenshots: List of relative paths to feature screenshots.
            category: Primary category (e.g., "AI", "Security", "Utilities").
            environment_variables: List of required ENV vars.
            readiness: Development stage (e.g., "alpha", "beta", "stable").
        """
        self.name = name
        self.version = version
        self.description = description
        self.author = author

        # Legacy support for 'dependencies'
        self.dependencies = dependencies or []

        # New dependency system
        self.python_dependencies = python_dependencies or []
        self.plugin_dependencies = plugin_dependencies or {}

        # Resources
        self.required_resources = required_resources or []
        self.optional_resources = optional_resources or []

        # Versioning
        self.min_core_version = min_core_version
        self.max_core_version = max_core_version

        # Metadata
        self.homepage = homepage
        self.license = license
        self.tags = tags or []

        # Professional Upgrade Fields
        self.icon = icon
        self.screenshots = screenshots or []
        self.category = category
        self.environment_variables = environment_variables or []
        self.readiness = readiness

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize metadata to a dictionary for API or logging export.
        """
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "dependencies": self.dependencies,  # Legacy
            "python_dependencies": self.python_dependencies,
            "plugin_dependencies": self.plugin_dependencies,
            "required_resources": self.required_resources,
            "optional_resources": self.optional_resources,
            "min_core_version": self.min_core_version,
            "max_core_version": self.max_core_version,
            "homepage": self.homepage,
            "license": self.license,
            "tags": self.tags,
            "icon": self.icon,
            "screenshots": self.screenshots,
            "category": self.category,
            "environment_variables": self.environment_variables,
            "readiness": self.readiness,
        }

    @classmethod
    def from_file(cls, path: Path) -> "PluginMetadata":
        """
        Load metadata from a manifest file.

        Args:
            path: Path to the manifest file (.yaml, .yml, or .json).

        Returns:
            PluginMetadata instance.
        """
        if path.suffix in (".yaml", ".yml"):
            import yaml

            with open(path, "r", encoding="utf-8") as mf:
                data = yaml.safe_load(mf)
        else:
            import json

            with open(path, "r", encoding="utf-8") as mf:
                data = json.load(mf)

        return cls(
            name=data.get("name", ""),
            version=data.get("version", "0.1.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            dependencies=data.get("dependencies"),
            required_resources=data.get("required_resources"),
            optional_resources=data.get("optional_resources"),
            python_dependencies=data.get("python_dependencies"),
            plugin_dependencies=data.get("plugin_dependencies"),
            min_core_version=data.get("min_core_version"),
            max_core_version=data.get("max_core_version"),
            homepage=data.get("homepage", ""),
            license=data.get("license", ""),
            tags=data.get("tags"),
            icon=data.get("icon", ""),
            screenshots=data.get("screenshots"),
            category=data.get("category", "Generic"),
            environment_variables=data.get("environment_variables"),
            readiness=data.get("readiness", "stable"),
        )

    def to_json_file(self, path: Path) -> None:
        """
        Save metadata to a manifest file.

        Args:
            path: Path to the manifest JSON file to write.
        """
        import json

        data = self.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)


class Plugin(ABC):
    """
    Abstract Base Class for all BaselithCore extensions.

    A Plugin is a self-contained module that can register:
    - AI Agents: Custom thinkers or tools.
    - Web Routes: FastAPI endpoints for custom logic.
    - UI Components: Frontend widgets or full pages.
    - Graph Schemas: Entity and relationship definitions for Knowledge Graphs.
    - Flow Handlers: Workflow logic triggered by intent detection.
    """

    def __init__(self):
        """
        Initialize the base plugin state. Internal use only.
        """
        self._initialized = False
        self._config: Dict[str, Any] = {}

    @property
    def metadata(self) -> PluginMetadata:
        """
        Define the plugin's identity by reading its manifest file.

        Returns:
            PluginMetadata: The static metadata for this plugin.
        """
        import sys

        # Try to find the directory where the subclass is defined
        module_name = self.__class__.__module__
        module = sys.modules.get(module_name)

        if module and hasattr(module, "__file__") and module.__file__:
            plugin_dir = Path(module.__file__).parent

            # Look for manifest.yaml first (preferred), then manifest.json
            manifest_yaml_path = plugin_dir / "manifest.yaml"
            manifest_yml_path = plugin_dir / "manifest.yml"
            manifest_json_path = plugin_dir / "manifest.json"

            if manifest_yaml_path.exists():
                return PluginMetadata.from_file(manifest_yaml_path)
            elif manifest_yml_path.exists():
                return PluginMetadata.from_file(manifest_yml_path)
            elif manifest_json_path.exists():
                return PluginMetadata.from_file(manifest_json_path)

            raise RuntimeError(
                f"Manifest file not found in {plugin_dir}. A manifest.yaml or manifest.json must exist for the plugin '{self.__class__.__name__}'."
            )

        raise RuntimeError(
            f"Could not determine plugin directory to read manifest for '{self.__class__.__name__}'."
        )

    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Prepare the plugin for operation.

        This is the standard entry point called by the PluginLoader.
        Perform DB migrations, client initializations, or heavy resource
        loading here.

        Args:
            config: User-provided configuration from the system settings.
        """
        self._config = config
        self._initialized = True
        logger.info(
            f"Plugin '{self.metadata.name}' v{self.metadata.version} initialized"
        )

    async def shutdown(self) -> None:
        """
        Gracefully stop the plugin and release resources.

        Called when the system is shutting down or the plugin is disabled.
        Implement custom cleanup logic (e.g., closing file handles).
        """
        self._initialized = False
        logger.info(f"Plugin '{self.metadata.name}' shutdown")

    def is_initialized(self) -> bool:
        """
        True if the initialize() method has been successfully called.
        """
        return self._initialized

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Retrieve a value from the plugin-specific configuration.

        Args:
            key: Config key name.
            default: Fallback value if key is missing.
        """
        return self._config.get(key, default)

    def validate_dependencies(self, available_plugins: List[str]) -> bool:
        """
        Check if the environment meets the plugin's requirements.

        Args:
            available_plugins: List of names of plugins currently in the registry.

        Returns:
            bool: True if all 'dependencies' are present.
        """
        for dep in self.metadata.dependencies:
            if dep == "core":
                continue
            if dep not in available_plugins:
                logger.error(f"Plugin '{self.metadata.name}' missing dependency: {dep}")
                return False
        return True

    def get_agents(self) -> List[Any]:
        """
        Expose AI agents to the Orchestrator.

        Returns:
            List[Any]: List of agent instances or factories.
        """
        return []

    def get_routers(self) -> List[Any]:
        """
        Expose FastAPI routers to the main web application.

        Returns:
            List[Any]: List of APIRouter instances.
        """
        return []

    def get_router_prefix(self) -> str:
        """
        Determine the URL mount point for plugin routes.

        Returns:
            str: Defaults to "/api/{plugin_name}".
        """
        return f"/api/{self.metadata.name}"

    def get_entity_types(self) -> List[Dict[str, Any]]:
        """
        Define custom Knowledge Graph node types.

        Returns:
            List[Dict]: Entity definitions (e.g., {"name": "Document", "properties": [...]}).
        """
        return []

    def get_relationship_types(self) -> List[Dict[str, Any]]:
        """
        Define custom Knowledge Graph edge types.

        Returns:
            List[Dict]: Relationship definitions.
        """
        return []

    def get_intent_patterns(self) -> List[Dict[str, Any]]:
        """
        Register NLP patterns for intent classification.

        Returns:
            List[Dict]: Intent mappings with patterns and descriptions.
        """
        return []

    def get_flow_handlers(self) -> Dict[str, Any]:
        """
        Map intents to execution logic.

        Returns:
            Dict[str, Any]: Map of intent names to handler classes/instances.
        """
        return {}

    def get_config_schema(self) -> Dict[str, Any]:
        """
        Expose a JSON Schema for configuration validation.

        Used by the UI/CLI to validate user-provided settings before
        calling initialize().

        Returns:
            Dict: standard JSON Schema object.
        """
        return {}

    def get_static_assets_path(self) -> Optional[Path]:
        """
        Expose local directory for serving frontend files.

        Returns:
            Optional[Path]: Absolute path to the directory containing assets.
        """
        return None

    def get_stylesheets(self) -> List[str]:
        """
        Specify CSS files to be injected into the main dashboard.

        Returns:
            List[str]: Relative paths within the static assets directory.
        """
        return []

    def get_scripts(self) -> List[str]:
        """
        Specify JavaScript files for frontend injection.

        Returns:
            List[str]: Relative paths within the static assets directory.
        """
        return []

    def get_ui_tabs(self) -> List[Dict[str, str]]:
        """
        Register navigation items in the admin sidebar.

        Returns:
            List[Dict]: Tab definitions with 'id' and 'label'.
        """
        return []

    def __repr__(self) -> str:
        """
        String representation for debugging.
        """
        return f"<Plugin: {self.metadata.name} v{self.metadata.version}>"
