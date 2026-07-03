"""Plugin lookup and query functionality.

Contains all getter methods for retrieving plugins and their components.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .interface import Plugin
    from .resource_analyzer import PluginDiscovery


class LookupMixin:
    """Mixin providing lookup/query functionality.

    This mixin is designed to be used with PluginRegistry and provides
    all getter methods for retrieving plugins and their components.
    """

    # These will be provided by the main class
    _plugins: dict[str, Plugin]
    _agents: dict[str, Any]
    _routers: list[Any]
    _entity_types: dict[str, dict[str, Any]]
    _relationship_types: dict[str, dict[str, Any]]
    _intent_patterns: dict[str, dict[str, Any]]
    _flow_handlers: dict[str, Any]
    _static_paths: dict[str, Path]
    _ui_tabs: dict[str, list[dict[str, str]]]
    _discovered_plugins: dict[str, PluginDiscovery]
    _discovered_entity_types: dict[str, dict[str, Any]]
    _discovered_relationship_types: dict[str, dict[str, Any]]
    _discovered_intent_patterns: dict[str, dict[str, Any]]
    _discovered_flow_handlers: dict[str, Any]
    _discovered_static_paths: dict[str, Path]
    _discovered_ui_tabs: dict[str, list[dict[str, str]]]
    _discovered_entity_type_owners: dict[str, str]
    _discovered_relationship_type_owners: dict[str, str]
    _discovered_intent_pattern_owners: dict[str, str]
    _discovered_flow_handler_owners: dict[str, str]
    _suppressed_discovered_plugins: set[str]

    def _visible_discoveries(self) -> dict[str, PluginDiscovery]:
        """Return discoveries that are not temporarily suppressed."""
        return {
            name: discovery
            for name, discovery in self._discovered_plugins.items()
            if name not in self._suppressed_discovered_plugins
        }

    def _visible_discovered_entity_types(self) -> dict[str, dict[str, Any]]:
        """Return discovered entity types from visible plugins only."""
        return {
            name: entity_type
            for name, entity_type in self._discovered_entity_types.items()
            if self._discovered_entity_type_owners.get(name)
            not in self._suppressed_discovered_plugins
        }

    def _visible_discovered_relationship_types(self) -> dict[str, dict[str, Any]]:
        """Return discovered relationship types from visible plugins only."""
        return {
            name: relationship_type
            for name, relationship_type in self._discovered_relationship_types.items()
            if self._discovered_relationship_type_owners.get(name)
            not in self._suppressed_discovered_plugins
        }

    def _visible_discovered_intent_patterns(self) -> dict[str, dict[str, Any]]:
        """Return discovered intent patterns from visible plugins only."""
        return {
            name: intent
            for name, intent in self._discovered_intent_patterns.items()
            if self._discovered_intent_pattern_owners.get(name)
            not in self._suppressed_discovered_plugins
        }

    def _visible_discovered_flow_handlers(self) -> dict[str, Any]:
        """Return discovered flow handlers from visible plugins only."""
        return {
            name: handler
            for name, handler in self._discovered_flow_handlers.items()
            if self._discovered_flow_handler_owners.get(name)
            not in self._suppressed_discovered_plugins
        }

    def get(self, plugin_name: str) -> Plugin | None:
        """
        Get a plugin by name.

        Args:
            plugin_name: Name of plugin to retrieve

        Returns:
            Plugin instance or None if not found
        """
        return self._plugins.get(plugin_name)

    def get_all(self) -> list[Plugin]:
        """
        Get all registered plugins.

        Returns:
            List of all plugin instances
        """
        return list(self._plugins.values())

    def get_agent(self, agent_name: str) -> Any | None:
        """
        Get an agent by name.

        Args:
            agent_name: Name of agent to retrieve

        Returns:
            Agent instance or None if not found
        """
        return self._agents.get(agent_name)

    def get_all_agents(self) -> dict[str, Any]:
        """
        Get all registered agents.

        Returns:
            Dictionary mapping agent names to instances
        """
        return self._agents.copy()

    def get_all_routers(self) -> list[Any]:
        """
        Get all registered routers.

        Returns:
            List of router instances
        """
        return self._routers.copy()

    def get_entity_type(self, type_name: str) -> dict[str, Any] | None:
        """
        Get entity type definition.

        Args:
            type_name: Name of entity type

        Returns:
            Entity type definition or None if not found
        """
        return self._entity_types.get(
            type_name
        ) or self._visible_discovered_entity_types().get(type_name)

    def get_all_entity_types(self) -> dict[str, dict[str, Any]]:
        """
        Get all registered entity types.

        Returns:
            Dictionary mapping type names to definitions
        """
        entity_types = self._visible_discovered_entity_types()
        entity_types.update(self._entity_types)
        return entity_types

    def get_relationship_type(self, type_name: str) -> dict[str, Any] | None:
        """
        Get relationship type definition.

        Args:
            type_name: Name of relationship type

        Returns:
            Relationship type definition or None if not found
        """
        return self._relationship_types.get(
            type_name
        ) or self._visible_discovered_relationship_types().get(type_name)

    def get_all_relationship_types(self) -> dict[str, dict[str, Any]]:
        """
        Get all registered relationship types.

        Returns:
            Dictionary mapping type names to definitions
        """
        relationship_types = self._visible_discovered_relationship_types()
        relationship_types.update(self._relationship_types)
        return relationship_types

    def get_intent_pattern(self, intent_name: str) -> dict[str, Any] | None:
        """
        Get intent pattern definition.

        Args:
            intent_name: Name of intent

        Returns:
            Intent pattern definition or None if not found
        """
        return self._intent_patterns.get(
            intent_name
        ) or self._visible_discovered_intent_patterns().get(intent_name)

    def get_all_intent_patterns(self) -> dict[str, dict[str, Any]]:
        """
        Get all registered intent patterns.

        Returns:
            Dictionary mapping intent names to definitions
        """
        intent_patterns = self._visible_discovered_intent_patterns()
        intent_patterns.update(self._intent_patterns)
        return intent_patterns

    def get_flow_handler(self, intent_name: str) -> Any | None:
        """
        Get flow handler for an intent.

        Args:
            intent_name: Name of intent

        Returns:
            Flow handler instance or None if not found
        """
        return self._flow_handlers.get(
            intent_name
        ) or self._visible_discovered_flow_handlers().get(intent_name)

    def get_all_flow_handlers(self) -> dict[str, Any]:
        """
        Get all registered flow handlers.

        Returns:
            Dictionary mapping intent names to handler instances
        """
        flow_handlers = self._visible_discovered_flow_handlers()
        flow_handlers.update(self._flow_handlers)
        return flow_handlers

    def get_all_static_paths(self) -> dict[str, Path]:
        """
        Get all registered static asset paths.

        Returns:
            Dictionary mapping plugin names to static directories
        """
        static_paths = self._discovered_static_paths.copy()
        static_paths.update(self._static_paths)
        return static_paths

    def get_frontend_manifest(self) -> dict[str, Any]:
        """
        Get manifest of all plugin frontend assets for injection.

        This manifest can be consumed by the frontend to dynamically
        load plugin-provided CSS and JavaScript files.

        Returns:
            Dictionary with plugin assets organized by plugin name:
            {
                "plugins": {
                    "plugin-name": {
                        "base_path": "/plugins/plugin-name/static",
                        "stylesheets": ["main.css"],
                        "scripts": ["main.js"]
                    }
                }
            }
        """
        manifest: dict[str, Any] = {"plugins": {}}

        visible_discoveries = self._visible_discoveries()

        for plugin_name, discovery in visible_discoveries.items():
            if plugin_name in self._plugins:
                continue

            static_path = self._discovered_static_paths.get(plugin_name)
            stylesheets = discovery.stylesheets
            scripts = discovery.scripts

            if (
                static_path and static_path.exists()
            ) or plugin_name in self._discovered_ui_tabs:
                manifest["plugins"][plugin_name] = {
                    "base_path": (
                        f"/plugins/{plugin_name}/static" if static_path else None
                    ),
                    "stylesheets": stylesheets,
                    "scripts": scripts,
                    "ui_tabs": self._discovered_ui_tabs.get(plugin_name, []),
                    "version": discovery.metadata.version,
                }

        for plugin in self._plugins.values():
            name = plugin.metadata.name
            static_path = plugin.get_static_assets_path()
            stylesheets = plugin.get_stylesheets()
            scripts = plugin.get_scripts()

            # Only include plugins with frontend assets (or UI tabs)
            if (static_path and static_path.exists()) or name in self._ui_tabs:
                manifest["plugins"][name] = {
                    "base_path": f"/plugins/{name}/static" if static_path else None,
                    "stylesheets": stylesheets,
                    "scripts": scripts,
                    "ui_tabs": self._ui_tabs.get(name, []),
                    "version": plugin.metadata.version,
                }

        return manifest

    def list_plugins(self) -> list[dict[str, Any]]:
        """
        List all registered plugins with metadata.

        Returns:
            List of plugin metadata dictionaries
        """
        plugins: dict[str, dict[str, Any]] = {}

        for plugin_name, discovery in self._visible_discoveries().items():
            plugins[plugin_name] = {
                "name": plugin_name,
                "version": discovery.metadata.version,
                "description": discovery.metadata.description,
                "author": discovery.metadata.author,
                "initialized": False,
            }

        for plugin in self._plugins.values():
            plugins[plugin.metadata.name] = {
                "name": plugin.metadata.name,
                "version": plugin.metadata.version,
                "description": plugin.metadata.description,
                "author": plugin.metadata.author,
                "initialized": plugin.is_initialized(),
            }

        return list(plugins.values())
