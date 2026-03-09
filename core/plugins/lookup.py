"""Plugin lookup and query functionality.

Contains all getter methods for retrieving plugins and their components.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .interface import Plugin


class LookupMixin:
    """Mixin providing lookup/query functionality.

    This mixin is designed to be used with PluginRegistry and provides
    all getter methods for retrieving plugins and their components.
    """

    # These will be provided by the main class
    _plugins: Dict[str, "Plugin"]
    _agents: Dict[str, Any]
    _routers: List[Any]
    _entity_types: Dict[str, Dict[str, Any]]
    _relationship_types: Dict[str, Dict[str, Any]]
    _intent_patterns: Dict[str, Dict[str, Any]]
    _flow_handlers: Dict[str, Any]
    _static_paths: Dict[str, Path]

    def get(self, plugin_name: str) -> Optional["Plugin"]:
        """
        Get a plugin by name.

        Args:
            plugin_name: Name of plugin to retrieve

        Returns:
            Plugin instance or None if not found
        """
        return self._plugins.get(plugin_name)

    def get_all(self) -> List["Plugin"]:
        """
        Get all registered plugins.

        Returns:
            List of all plugin instances
        """
        return list(self._plugins.values())

    def get_agent(self, agent_name: str) -> Optional[Any]:
        """
        Get an agent by name.

        Args:
            agent_name: Name of agent to retrieve

        Returns:
            Agent instance or None if not found
        """
        return self._agents.get(agent_name)

    def get_all_agents(self) -> Dict[str, Any]:
        """
        Get all registered agents.

        Returns:
            Dictionary mapping agent names to instances
        """
        return self._agents.copy()

    def get_all_routers(self) -> List[Any]:
        """
        Get all registered routers.

        Returns:
            List of router instances
        """
        return self._routers.copy()

    def get_entity_type(self, type_name: str) -> Optional[Dict[str, Any]]:
        """
        Get entity type definition.

        Args:
            type_name: Name of entity type

        Returns:
            Entity type definition or None if not found
        """
        return self._entity_types.get(type_name)

    def get_all_entity_types(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all registered entity types.

        Returns:
            Dictionary mapping type names to definitions
        """
        return self._entity_types.copy()

    def get_relationship_type(self, type_name: str) -> Optional[Dict[str, Any]]:
        """
        Get relationship type definition.

        Args:
            type_name: Name of relationship type

        Returns:
            Relationship type definition or None if not found
        """
        return self._relationship_types.get(type_name)

    def get_all_relationship_types(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all registered relationship types.

        Returns:
            Dictionary mapping type names to definitions
        """
        return self._relationship_types.copy()

    def get_intent_pattern(self, intent_name: str) -> Optional[Dict[str, Any]]:
        """
        Get intent pattern definition.

        Args:
            intent_name: Name of intent

        Returns:
            Intent pattern definition or None if not found
        """
        return self._intent_patterns.get(intent_name)

    def get_all_intent_patterns(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all registered intent patterns.

        Returns:
            Dictionary mapping intent names to definitions
        """
        return self._intent_patterns.copy()

    def get_flow_handler(self, intent_name: str) -> Optional[Any]:
        """
        Get flow handler for an intent.

        Args:
            intent_name: Name of intent

        Returns:
            Flow handler instance or None if not found
        """
        return self._flow_handlers.get(intent_name)

    def get_all_flow_handlers(self) -> Dict[str, Any]:
        """
        Get all registered flow handlers.

        Returns:
            Dictionary mapping intent names to handler instances
        """
        return self._flow_handlers.copy()

    def get_all_static_paths(self) -> Dict[str, Path]:
        """
        Get all registered static asset paths.

        Returns:
            Dictionary mapping plugin names to static directories
        """
        return self._static_paths.copy()

    def get_frontend_manifest(self) -> Dict[str, Any]:
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
        manifest: Dict[str, Any] = {"plugins": {}}

        for plugin in self._plugins.values():
            name = plugin.metadata.name
            static_path = plugin.get_static_assets_path()
            stylesheets = plugin.get_stylesheets()
            scripts = plugin.get_scripts()

            # Only include plugins with frontend assets
            if static_path and static_path.exists() and (stylesheets or scripts):
                manifest["plugins"][name] = {
                    "base_path": f"/plugins/{name}/static",
                    "stylesheets": stylesheets,
                    "scripts": scripts,
                    "version": plugin.metadata.version,
                }

        return manifest

    def list_plugins(self) -> List[Dict[str, Any]]:
        """
        List all registered plugins with metadata.

        Returns:
            List of plugin metadata dictionaries
        """
        return [
            {
                "name": plugin.metadata.name,
                "version": plugin.metadata.version,
                "description": plugin.metadata.description,
                "author": plugin.metadata.author,
                "initialized": plugin.is_initialized(),
            }
            for plugin in self._plugins.values()
        ]
