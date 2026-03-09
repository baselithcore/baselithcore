"""Plugin component registration logic.

Contains all component registration methods for the PluginRegistry.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from .interface import Plugin

logger = get_logger(__name__)


class RegistrationMixin:
    """Mixin providing component registration functionality.

    This mixin is designed to be used with PluginRegistry and provides
    all methods related to registering plugin components (agents, routers,
    entity types, etc.).
    """

    # These will be provided by the main class
    _agents: Dict[str, Any]
    _routers: List[Any]
    _entity_types: Dict[str, Dict[str, Any]]
    _relationship_types: Dict[str, Dict[str, Any]]
    _intent_patterns: Dict[str, Dict[str, Any]]
    _flow_handlers: Dict[str, Any]
    _static_paths: Dict[str, Path]

    def _register_agents(self, plugin: "Plugin") -> None:
        """Register agents from plugin."""
        for agent in plugin.get_agents():
            agent_name = getattr(agent, "name", None) or plugin.metadata.name
            self._agents[agent_name] = agent
            logger.debug(f"Registered agent: {agent_name} from {plugin.metadata.name}")

    def _register_routers(self, plugin: "Plugin") -> None:
        """Register routers from plugin."""
        for router in plugin.get_routers():
            self._routers.append(router)
            logger.debug(f"Registered router from {plugin.metadata.name}")

    def _register_entity_types(self, plugin: "Plugin") -> None:
        """Register entity types from plugin."""
        for entity_type in plugin.get_entity_types():
            type_name = entity_type.get("type")
            if type_name:
                self._entity_types[type_name] = entity_type
                logger.debug(
                    f"Registered entity type: {type_name} from {plugin.metadata.name}"
                )

    def _register_relationship_types(self, plugin: "Plugin") -> None:
        """Register relationship types from plugin."""
        for rel_type in plugin.get_relationship_types():
            type_name = rel_type.get("type")
            if type_name:
                self._relationship_types[type_name] = rel_type
                logger.debug(
                    f"Registered relationship type: {type_name} from {plugin.metadata.name}"
                )

    def _register_intent_patterns(self, plugin: "Plugin") -> None:
        """Register intent patterns from plugin."""
        for intent in plugin.get_intent_patterns():
            intent_name = intent.get("name")
            if intent_name:
                self._intent_patterns[intent_name] = intent
                logger.debug(
                    f"Registered intent: {intent_name} from {plugin.metadata.name}"
                )

    def _register_flow_handlers(self, plugin: "Plugin") -> None:
        """Register flow handlers from plugin."""
        for intent_name, handler in plugin.get_flow_handlers().items():
            if intent_name in self._flow_handlers:
                logger.warning(
                    f"Flow handler for '{intent_name}' already registered, overwriting"
                )
            self._flow_handlers[intent_name] = handler
            logger.debug(
                f"Registered flow handler: {intent_name} from {plugin.metadata.name}"
            )

    def _register_static_assets(self, plugin: "Plugin") -> None:
        """Register static assets directory from plugin."""
        static_path = plugin.get_static_assets_path()
        if static_path and static_path.exists():
            self._static_paths[plugin.metadata.name] = static_path
            logger.debug(
                f"Registered static assets: {static_path} from {plugin.metadata.name}"
            )

    def register_all_components(self, plugin: "Plugin") -> None:
        """Register all components from a plugin."""
        self._register_agents(plugin)
        self._register_routers(plugin)
        self._register_entity_types(plugin)
        self._register_relationship_types(plugin)
        self._register_intent_patterns(plugin)
        self._register_flow_handlers(plugin)
        self._register_static_assets(plugin)

    def _cleanup_plugin_components(self, plugin_name: str) -> None:
        """Clean up all components registered by a plugin."""
        # Remove agents from this plugin
        agents_to_remove = [
            name
            for name, agent in self._agents.items()
            if getattr(agent, "_plugin_name", None) == plugin_name
            or name == plugin_name
        ]
        for name in agents_to_remove:
            del self._agents[name]

        # Remove static paths
        self._static_paths.pop(plugin_name, None)

        # Note: Routers cannot be easily removed from FastAPI after registration
        # This would require application restart for full cleanup

        logger.debug(f"Cleaned up components for plugin: {plugin_name}")
