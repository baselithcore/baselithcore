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

from core.observability.logging import get_logger
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from .interface import Plugin
from .registration import RegistrationMixin
from .health import HealthMixin
from .lookup import LookupMixin

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
        self._plugins: Dict[str, Plugin] = {}
        self._agents: Dict[str, Any] = {}
        self._routers: List[Any] = []
        self._entity_types: Dict[str, Dict[str, Any]] = {}
        self._relationship_types: Dict[str, Dict[str, Any]] = {}
        self._intent_patterns: Dict[str, Dict[str, Any]] = {}
        self._flow_handlers: Dict[str, Any] = {}  # intent_name -> handler
        self._static_paths: Dict[str, Path] = {}  # plugin_name -> static dir

    def register(self, plugin: Plugin) -> None:
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

            if not plugin.is_initialized():
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

            # Delegate to RegistrationMixin for component extraction.
            self.register_all_components(plugin)

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
        new_config: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Atomic reload of a plugin's configuration and components.
        """
        with self._lock:
            return await HealthMixin.reload_plugin(self, plugin_name, new_config)

    def health_check(self, plugin_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve operational status for one or all plugins.
        """
        with self._lock:
            return HealthMixin.health_check(self, plugin_name)

    def get_plugin_version(self, plugin_name: str) -> Optional[str]:
        """
        Quick lookup for a plugin's version string.
        """
        with self._lock:
            return HealthMixin.get_plugin_version(self, plugin_name)

    def get(self, plugin_name: str) -> Optional[Plugin]:
        """
        Retrieve a raw Plugin instance by its unique name.
        """
        with self._lock:
            return LookupMixin.get(self, plugin_name)

    def get_all(self) -> List[Plugin]:
        """
        Retrieve a list of all currently registered and active plugins.
        """
        with self._lock:
            return LookupMixin.get_all(self)

    def get_agent(self, agent_name: str) -> Optional[Any]:
        """
        Retrieve an AI agent instance by its registered name.
        """
        with self._lock:
            return LookupMixin.get_agent(self, agent_name)

    def get_all_agents(self) -> Dict[str, Any]:
        """
        Retrieve a mapping of all registered agent names to instances.
        """
        with self._lock:
            return LookupMixin.get_all_agents(self)

    def get_all_routers(self) -> List[Any]:
        """
        Retrieve all registered FastAPI routers for system-wide mount.
        """
        with self._lock:
            return LookupMixin.get_all_routers(self)

    def get_entity_type(self, type_name: str) -> Optional[Dict[str, Any]]:
        """
        Lookup a specific Knowledge Graph entity type definition.
        """
        with self._lock:
            return LookupMixin.get_entity_type(self, type_name)

    def get_all_entity_types(self) -> Dict[str, Dict[str, Any]]:
        """
        Retrieve all registered entity types for schema generation.
        """
        with self._lock:
            return LookupMixin.get_all_entity_types(self)

    def get_relationship_type(self, type_name: str) -> Optional[Dict[str, Any]]:
        """
        Lookup a specific Knowledge Graph relationship type definition.
        """
        with self._lock:
            return LookupMixin.get_relationship_type(self, type_name)

    def get_all_relationship_types(self) -> Dict[str, Dict[str, Any]]:
        """
        Retrieve all registered relationship types for schema generation.
        """
        with self._lock:
            return LookupMixin.get_all_relationship_types(self)

    def get_intent_pattern(self, intent_name: str) -> Optional[Dict[str, Any]]:
        """
        Lookup NLP patterns associated with a specific intent.
        """
        with self._lock:
            return LookupMixin.get_intent_pattern(self, intent_name)

    def get_all_intent_patterns(self) -> Dict[str, Dict[str, Any]]:
        """
        Retrieve all intent patterns for training/classification.
        """
        with self._lock:
            return LookupMixin.get_all_intent_patterns(self)

    def get_flow_handler(self, intent_name: str) -> Optional[Any]:
        """
        Retrieve the workflow handler responsible for an intent.
        """
        with self._lock:
            return LookupMixin.get_flow_handler(self, intent_name)

    def get_all_flow_handlers(self) -> Dict[str, Any]:
        """
        Retrieve all registered flow handlers.
        """
        with self._lock:
            return LookupMixin.get_all_flow_handlers(self)

    def get_all_static_paths(self) -> Dict[str, Path]:
        """
        Retrieve mapping of plugin names to their static asset directories.
        """
        with self._lock:
            return LookupMixin.get_all_static_paths(self)

    def get_frontend_manifest(self) -> Dict[str, Any]:
        """
        Generate a manifest of UI tabs, scripts, and styles for the frontend.
        """
        with self._lock:
            return LookupMixin.get_frontend_manifest(self)

    def list_plugins(self) -> List[Dict[str, Any]]:
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
