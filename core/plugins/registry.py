"""
Plugin registry for managing loaded plugins.

This module provides the central authority for plugin management in BaselithCore.
It orchestrates the lifecycle of extensions, ensuring thread-safe registration,
lookup, and health monitoring.

The registry architecture uses Mixins to separate concerns:
- `RegistrationMixin`: High-level logic for binding plugins and their components.
- `HealthMixin`: Operational monitoring and reloading capabilities.
- `LookupMixin`: Query interface for retrieving agents, routers, and handlers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from threading import RLock
from typing import Any

from core.observability.logging import get_logger

from .health import HealthMixin
from .interface import Plugin
from .lookup import LookupMixin
from .registration import RegistrationMixin, _LazyFlowHandlerProxy
from .resource_analyzer import PluginDiscovery

logger = get_logger(__name__)


class PluginRegistry(RegistrationMixin, HealthMixin, LookupMixin):
    """
    Centralized Hub for BaselithCore Extensibility.

    The Registry maintains the global state of all active plugins and provides
    a thread-safe way for core services to discover and interact with
    plugin-provided features (AI agents, API routes, Graph schemas, etc.).

    Features:
    - Thread-safe operations via RLock.
    - Component discovery (Agents, Routers, Handlers).
    - Multi-tenant aware metadata isolation.
    - Static asset mapping for the frontend.
    - Lifecycle management (Load, Reload, Unload).
    """

    def __init__(self) -> None:
        """
        Initialize the registry with empty internal state.
        """
        self._lock = RLock()
        self._plugins: dict[str, Plugin] = {}
        self._agents: dict[str, Any] = {}
        self._routers: list[Any] = []
        self._entity_types: dict[str, dict[str, Any]] = {}
        self._relationship_types: dict[str, dict[str, Any]] = {}
        self._intent_patterns: dict[str, dict[str, Any]] = {}
        self._flow_handlers: dict[str, Any] = {}  # intent_name -> handler
        self._static_paths: dict[str, Path] = {}  # plugin_name -> static dir
        self._ui_tabs: dict[
            str, list[dict[str, str]]
        ] = {}  # plugin_name -> list of tabs
        self._entity_type_owners: dict[str, str] = {}
        self._relationship_type_owners: dict[str, str] = {}
        self._intent_pattern_owners: dict[str, str] = {}
        self._flow_handler_owners: dict[str, str] = {}
        self._static_path_owners: dict[str, str] = {}
        self._ui_tab_owners: dict[str, str] = {}
        self._discovered_plugins: dict[str, PluginDiscovery] = {}
        self._plugin_directories: dict[str, Path] = {}
        self._discovered_entity_types: dict[str, dict[str, Any]] = {}
        self._discovered_relationship_types: dict[str, dict[str, Any]] = {}
        self._discovered_intent_patterns: dict[str, dict[str, Any]] = {}
        self._discovered_flow_handlers: dict[str, Any] = {}
        self._discovered_static_paths: dict[str, Path] = {}
        self._discovered_ui_tabs: dict[str, list[dict[str, str]]] = {}
        self._discovered_entity_type_owners: dict[str, str] = {}
        self._discovered_relationship_type_owners: dict[str, str] = {}
        self._discovered_intent_pattern_owners: dict[str, str] = {}
        self._discovered_flow_handler_owners: dict[str, str] = {}
        self._suppressed_discovered_plugins: set[str] = set()
        self._activation_callback: Callable[[str], Awaitable[bool]] | None = None

    def set_activation_callback(
        self, callback: Callable[[str], Awaitable[bool]]
    ) -> None:
        """Register the async callback used to activate cold plugins."""
        self._activation_callback = callback

    async def ensure_plugin_active(self, plugin_name: str) -> bool:
        """Ensure a registered plugin has completed initialization."""
        plugin = self.get(plugin_name)
        if plugin is not None and plugin.is_initialized():
            return True
        if plugin is None and plugin_name not in self._discovered_plugins:
            return False
        if self._activation_callback is None:
            raise RuntimeError(
                f"Plugin '{plugin_name}' requires activation but no activation callback is configured."
            )
        return await self._activation_callback(plugin_name)

    def register_discovered_plugin(self, discovery: PluginDiscovery) -> None:
        """Register static plugin capabilities discovered without importing code."""
        with self._lock:
            plugin_name = discovery.name
            self._discovered_plugins[plugin_name] = discovery
            self._plugin_directories[plugin_name] = discovery.plugin_dir
            self._suppressed_discovered_plugins.discard(plugin_name)

            for entity_name, entity_type in discovery.entity_types.items():
                self._discovered_entity_types[entity_name] = entity_type
                self._discovered_entity_type_owners[entity_name] = plugin_name

            for (
                relationship_name,
                relationship_type,
            ) in discovery.relationship_types.items():
                self._discovered_relationship_types[relationship_name] = (
                    relationship_type
                )
                self._discovered_relationship_type_owners[relationship_name] = (
                    plugin_name
                )

            for intent_name, intent_pattern in discovery.intent_patterns.items():
                self._discovered_intent_patterns[intent_name] = intent_pattern
                self._discovered_intent_pattern_owners[intent_name] = plugin_name

            for intent_name in discovery.flow_handler_names:
                self._discovered_flow_handlers[intent_name] = _LazyFlowHandlerProxy(
                    self,
                    plugin_name,
                    intent_name=intent_name,
                )
                self._discovered_flow_handler_owners[intent_name] = plugin_name

            if discovery.static_path and discovery.static_path.exists():
                self._discovered_static_paths[plugin_name] = discovery.static_path
            else:
                self._discovered_static_paths.pop(plugin_name, None)

            if discovery.ui_tabs:
                self._discovered_ui_tabs[plugin_name] = discovery.ui_tabs
            else:
                self._discovered_ui_tabs.pop(plugin_name, None)

            logger.info(
                "Registered discovered plugin metadata: %s v%s",
                plugin_name,
                discovery.metadata.version,
            )

    def suppress_discovered_plugin(self, plugin_name: str) -> None:
        """Hide discovered placeholders for a disabled plugin."""
        with self._lock:
            if plugin_name in self._discovered_plugins:
                self._suppressed_discovered_plugins.add(plugin_name)

    def unsuppress_discovered_plugin(self, plugin_name: str) -> None:
        """Expose discovered placeholders again for an enabled plugin."""
        with self._lock:
            self._suppressed_discovered_plugins.discard(plugin_name)

    def get_registered_flow_handler(self, intent_name: str) -> Any | None:
        """Return only a real runtime flow handler, excluding discovery placeholders."""
        with self._lock:
            return self._flow_handlers.get(intent_name)

    def get_plugin_directory(self, plugin_name: str) -> Path | None:
        """Resolve the filesystem directory for a plugin by logical name."""
        with self._lock:
            return self._plugin_directories.get(plugin_name)

    def get_discovered_plugin(self, plugin_name: str) -> PluginDiscovery | None:
        """Return static discovery data for a plugin, if available."""
        with self._lock:
            return self._discovered_plugins.get(plugin_name)

    def match_plugin_route(self, request_path: str) -> str | None:
        """Match a request path against discovered router prefixes."""
        with self._lock:
            candidates = []
            for plugin_name, discovery in self._discovered_plugins.items():
                if plugin_name in self._suppressed_discovered_plugins:
                    continue
                if not discovery.provides_routes or not discovery.router_prefix:
                    continue
                prefix = discovery.router_prefix.rstrip("/")
                if request_path == prefix or request_path.startswith(f"{prefix}/"):
                    candidates.append((len(prefix), plugin_name))

            if not candidates:
                return None

            candidates.sort(reverse=True)
            return candidates[0][1]

    def register(self, plugin: Plugin, require_initialized: bool = True) -> None:
        """
        Add a plugin instance to the active registry.

        This method validates the plugin's state and dependencies before
        decomposing it into its constituent components (agents, routers, etc.).

        Args:
            plugin: An initialized Plugin instance.

        Raises:
            ValueError: If the plugin name is a duplicate.
            ValueError: If the plugin has not been initialized.
            ValueError: If semantic dependencies are missing.
        """
        with self._lock:
            name = plugin.metadata.name

            if name in self._plugins:
                raise ValueError(f"Plugin '{name}' is already registered")

            if require_initialized and not plugin.is_initialized():
                raise ValueError(
                    f"Plugin '{name}' must be initialized before registration"
                )

            # Validate dependencies against currently registered plugins.
            available_plugins = list(self._plugins.keys())
            if not plugin.validate_dependencies(available_plugins):
                raise ValueError(
                    f"Plugin '{name}' has unmet dependencies: {plugin.metadata.dependencies}"
                )

            self._plugins[name] = plugin
            module_file = getattr(type(plugin), "__module__", None)
            module = None
            if module_file:
                import sys

                module = sys.modules.get(module_file)
            if module and (module_path := getattr(module, "__file__", None)):
                self._plugin_directories[name] = Path(module_path).parent

            # Delegate to RegistrationMixin for component extraction.
            self.register_all_components(plugin)

            self.unsuppress_discovered_plugin(name)

            logger.info(f"Registered plugin: {name} v{plugin.metadata.version}")

    async def unregister(self, plugin_name: str) -> None:
        """
        Safely remove a plugin and its associated components.

        Args:
            plugin_name: Unique identifier of the target plugin.
        """
        with self._lock:
            if plugin_name not in self._plugins:
                logger.warning(f"Plugin '{plugin_name}' not registered")
                return

            plugin = self._plugins[plugin_name]

            # 1. Component Cleanup: Remove agents, routes, etc., from global collections.
            self._cleanup_plugin_components(plugin_name)

            # 2. Lifecycle Stop: Call shutdown sequence on the plugin itself.
            await plugin.shutdown()
            del self._plugins[plugin_name]

            logger.info(f"Unregistered plugin: {plugin_name}")

    # --- Thread-Safe Mixin Overrides ---
    # These methods provide a thread-safe entry point to logic defined in Mixins.

    async def reload_plugin(
        self,
        plugin_name: str,
        new_config: dict[str, Any] | None = None,
    ) -> bool:
        """
        Atomic reload of a plugin's configuration and components.
        """
        with self._lock:
            return await HealthMixin.reload_plugin(self, plugin_name, new_config)

    def health_check(self, plugin_name: str | None = None) -> dict[str, Any]:
        """
        Retrieve operational status for one or all plugins.
        """
        with self._lock:
            return HealthMixin.health_check(self, plugin_name)

    def get_plugin_version(self, plugin_name: str) -> str | None:
        """
        Quick lookup for a plugin's version string.
        """
        with self._lock:
            return HealthMixin.get_plugin_version(self, plugin_name)

    def get(self, plugin_name: str) -> Plugin | None:
        """
        Retrieve a raw Plugin instance by its unique name.
        """
        with self._lock:
            return LookupMixin.get(self, plugin_name)

    def get_all(self) -> list[Plugin]:
        """
        Retrieve a list of all currently registered and active plugins.
        """
        with self._lock:
            return LookupMixin.get_all(self)

    def get_agent(self, agent_name: str) -> Any | None:
        """
        Retrieve an AI agent instance by its registered name.
        """
        with self._lock:
            return LookupMixin.get_agent(self, agent_name)

    def get_all_agents(self) -> dict[str, Any]:
        """
        Retrieve a mapping of all registered agent names to instances.
        """
        with self._lock:
            return LookupMixin.get_all_agents(self)

    def get_all_routers(self) -> list[Any]:
        """
        Retrieve all registered FastAPI routers for system-wide mount.
        """
        with self._lock:
            return LookupMixin.get_all_routers(self)

    def get_entity_type(self, type_name: str) -> dict[str, Any] | None:
        """
        Lookup a specific Knowledge Graph entity type definition.
        """
        with self._lock:
            return LookupMixin.get_entity_type(self, type_name)

    def get_all_entity_types(self) -> dict[str, dict[str, Any]]:
        """
        Retrieve all registered entity types for schema generation.
        """
        with self._lock:
            return LookupMixin.get_all_entity_types(self)

    def get_relationship_type(self, type_name: str) -> dict[str, Any] | None:
        """
        Lookup a specific Knowledge Graph relationship type definition.
        """
        with self._lock:
            return LookupMixin.get_relationship_type(self, type_name)

    def get_all_relationship_types(self) -> dict[str, dict[str, Any]]:
        """
        Retrieve all registered relationship types for schema generation.
        """
        with self._lock:
            return LookupMixin.get_all_relationship_types(self)

    def get_intent_pattern(self, intent_name: str) -> dict[str, Any] | None:
        """
        Lookup NLP patterns associated with a specific intent.
        """
        with self._lock:
            return LookupMixin.get_intent_pattern(self, intent_name)

    def get_all_intent_patterns(self) -> dict[str, dict[str, Any]]:
        """
        Retrieve all intent patterns for training/classification.
        """
        with self._lock:
            return LookupMixin.get_all_intent_patterns(self)

    def get_flow_handler(self, intent_name: str) -> Any | None:
        """
        Retrieve the workflow handler responsible for an intent.
        """
        with self._lock:
            return LookupMixin.get_flow_handler(self, intent_name)

    def get_all_flow_handlers(self) -> dict[str, Any]:
        """
        Retrieve all registered flow handlers.
        """
        with self._lock:
            return LookupMixin.get_all_flow_handlers(self)

    def get_all_static_paths(self) -> dict[str, Path]:
        """
        Retrieve mapping of plugin names to their static asset directories.
        """
        with self._lock:
            return LookupMixin.get_all_static_paths(self)

    def get_frontend_manifest(self) -> dict[str, Any]:
        """
        Generate a manifest of UI tabs, scripts, and styles for the frontend.
        """
        with self._lock:
            return LookupMixin.get_frontend_manifest(self)

    def list_plugins(self) -> list[dict[str, Any]]:
        """
        Retrieve a summary list of all plugins (ID, version, description) for the UI.
        """
        with self._lock:
            return LookupMixin.list_plugins(self)

    def __len__(self) -> int:
        """
        Returns the count of registered plugins.
        """
        with self._lock:
            return len(self._plugins)

    def __contains__(self, plugin_name: str) -> bool:
        """
        Syntactic sugar for checking plugin registration (e.g., 'auth' in registry).
        """
        with self._lock:
            return plugin_name in self._plugins
