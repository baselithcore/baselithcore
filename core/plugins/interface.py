"""
Base plugin interface for the BaselithCore framework.

This module defines the contract that all plugins must satisfy to be
integrated into the core system. It provides:
1. `PluginMetadata`: A structured containter for plugin identity and requirements.
2. `Plugin`: The abstract base class providing hooks for agents, UI, and APIs.
"""

from abc import ABC
from functools import cached_property
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger

# PluginMetadata lives in a sibling module to keep this file under the 500-LOC
# cap; re-exported here so ``from core.plugins.interface import PluginMetadata``
# keeps working for every existing caller.
from core.plugins._metadata import PluginMetadata

logger = get_logger(__name__)


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
        self._config: dict[str, Any] = {}

    @cached_property
    def metadata(self) -> PluginMetadata:
        """
        Define the plugin's identity by reading its manifest file.

        The manifest is parsed once and cached on the instance: a reload
        constructs a fresh plugin instance (see ``PluginLoader.load_plugin``),
        so the cache is invalidated automatically.

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

    def tenant_key(self) -> str:
        """Resolve the tenant key this plugin should scope its storage by.

        Honours the plugin's declared ``tenancy`` (``"shared"`` | ``"personal"``)
        and the bound identity context: ``"personal"`` plugins get a per-user
        key (1 user = 1 tenant) even on a shared deployment, ``"shared"`` plugins
        get the deployment-derived tenant. Identity-derived — never reads a
        client-supplied header. Call this everywhere the plugin would otherwise
        call ``get_current_tenant_id()`` for storage scoping.

        The declared mode may be overridden at runtime (e.g. from the auth
        admin console) via :func:`core.context.resolve_plugin_tenancy_mode`;
        absent any override it falls back to the manifest, so behaviour is
        unchanged on deployments that never set one.

        Returns:
            The tenant id to use in ``WHERE tenant_id = …`` / namespaces / paths.
        """
        from core.context import resolve_plugin_tenancy_mode, resolve_plugin_tenant

        declared = self.metadata.tenancy
        # System (infrastructure) plugins are EXEMPT from runtime tenancy
        # overrides: their scoping is platform-governed, and re-scoping one —
        # e.g. ``auth``, the tenancy source itself — would fracture tenant
        # isolation system-wide. They always use the declared manifest mode,
        # so even a hand-inserted override row can never re-scope them.
        if getattr(self.metadata, "system", False):
            return resolve_plugin_tenant(declared)
        mode = resolve_plugin_tenancy_mode(self.metadata.name, declared)
        return resolve_plugin_tenant(mode)

    async def initialize(self, config: dict[str, Any]) -> None:
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

    def validate_dependencies(self, available_plugins: list[str]) -> bool:
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

    def get_agents(self) -> list[Any]:
        """
        Expose AI agents to the Orchestrator.

        Returns:
            List[Any]: List of agent instances or factories.
        """
        return []

    def get_routers(self) -> list[Any]:
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

    def get_entity_types(self) -> list[dict[str, Any]]:
        """
        Define custom Knowledge Graph node types.

        Returns:
            List[Dict]: Entity definitions (e.g., {"name": "Document", "properties": [...]}).
        """
        return []

    def get_relationship_types(self) -> list[dict[str, Any]]:
        """
        Define custom Knowledge Graph edge types.

        Returns:
            List[Dict]: Relationship definitions.
        """
        return []

    def get_intent_patterns(self) -> list[dict[str, Any]]:
        """
        Register NLP patterns for intent classification.

        Returns:
            List[Dict]: Intent mappings with patterns and descriptions.
        """
        return []

    def get_flow_handlers(self) -> dict[str, Any]:
        """
        Map intents to execution logic.

        Returns:
            Dict[str, Any]: Map of intent names to handler classes/instances.
        """
        return {}

    def get_config_schema(self) -> dict[str, Any]:
        """
        Expose a JSON Schema for configuration validation.

        Used by the UI/CLI to validate user-provided settings before
        calling initialize().

        Returns:
            Dict: standard JSON Schema object.
        """
        return {}

    def get_static_assets_path(self) -> Path | None:
        """
        Expose local directory for serving frontend files.

        Returns:
            Optional[Path]: Absolute path to the directory containing assets.
        """
        return None

    def get_stylesheets(self) -> list[str]:
        """
        Specify CSS files to be injected into the main dashboard.

        Returns:
            List[str]: Relative paths within the static assets directory.
        """
        return []

    def get_scripts(self) -> list[str]:
        """
        Specify JavaScript files for frontend injection.

        Returns:
            List[str]: Relative paths within the static assets directory.
        """
        return []

    def get_ui_tabs(self) -> list[dict[str, str]]:
        """
        Register navigation items in the admin sidebar.

        Returns:
            List[Dict]: Tab definitions with 'id' and 'label'.
        """
        return []

    def get_mcp_tools(self) -> list[dict[str, Any]]:
        """
        Expose MCP tools to the core MCP server.

        Returns:
            List[Dict]: Tool definitions with 'name', 'description', 'input_schema', and 'handler'.
        """
        return []

    @classmethod
    def setup_app_middleware(cls, app: Any) -> None:
        """
        Hook invoked at app construction time to register Starlette middleware.

        Starlette finalises the middleware stack before lifespan starts, so any
        plugin that needs app-level middleware (CORS overrides, telemetry,
        per-path gates, …) must hook in here — the standard async ``initialize``
        runs too late (inside the lifespan, after the stack is frozen).

        The default implementation is a no-op. Override on a per-plugin basis
        and call ``app.add_middleware(...)`` from inside the override. The method
        is a ``classmethod`` so it can run without instantiating the plugin or
        paying its (potentially heavy) ``__init__`` cost.

        Args:
            app: The FastAPI/Starlette application under construction.
        """
        del app  # default no-op
        return None

    def __repr__(self) -> str:
        """
        String representation for debugging.
        """
        return f"<Plugin: {self.metadata.name} v{self.metadata.version}>"
