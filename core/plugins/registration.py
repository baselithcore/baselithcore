"""Plugin component registration logic.

Contains all component registration methods for the PluginRegistry.
"""

from __future__ import annotations

import inspect
from core.observability.logging import get_logger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from .interface import Plugin

logger = get_logger(__name__)


class _LazyFlowHandlerProxy:
    """Defers plugin activation until the handler is actually invoked."""

    def __init__(
        self,
        registry: Any,
        plugin_name: str,
        handler: Any = None,
        *,
        intent_name: str,
    ) -> None:
        self._registry = registry
        self._plugin_name = plugin_name
        self._handler = handler
        self._intent_name = intent_name

    async def handle(self, query: str, context: Dict[str, Any]) -> Any:
        activated = await self._registry.ensure_plugin_active(self._plugin_name)
        if not activated:
            raise RuntimeError(f"Plugin '{self._plugin_name}' could not be activated")

        target_handler = self._handler
        if target_handler is None:
            target_handler = self._registry.get_registered_flow_handler(
                self._intent_name
            )
            if target_handler is None or target_handler is self:
                raise RuntimeError(
                    f"Flow handler '{self._intent_name}' is not available after activating plugin '{self._plugin_name}'."
                )

        target = getattr(target_handler, "handle", target_handler)
        result = target(query, context)
        if inspect.isawaitable(result):
            return await result
        return result

    def __getattr__(self, name: str) -> Any:
        if self._handler is None:
            raise AttributeError(name)
        return getattr(self._handler, name)


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
    _ui_tabs: Dict[str, List[Dict[str, str]]]
    _entity_type_owners: Dict[str, str]
    _relationship_type_owners: Dict[str, str]
    _intent_pattern_owners: Dict[str, str]
    _flow_handler_owners: Dict[str, str]
    _static_path_owners: Dict[str, str]
    _ui_tab_owners: Dict[str, str]

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
                self._entity_type_owners[type_name] = plugin.metadata.name
                logger.debug(
                    f"Registered entity type: {type_name} from {plugin.metadata.name}"
                )

    def _register_relationship_types(self, plugin: "Plugin") -> None:
        """Register relationship types from plugin."""
        for rel_type in plugin.get_relationship_types():
            type_name = rel_type.get("type")
            if type_name:
                self._relationship_types[type_name] = rel_type
                self._relationship_type_owners[type_name] = plugin.metadata.name
                logger.debug(
                    f"Registered relationship type: {type_name} from {plugin.metadata.name}"
                )

    def _register_intent_patterns(self, plugin: "Plugin") -> None:
        """Register intent patterns from plugin."""
        for intent in plugin.get_intent_patterns():
            intent_name = intent.get("name")
            if intent_name:
                self._intent_patterns[intent_name] = intent
                self._intent_pattern_owners[intent_name] = plugin.metadata.name
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
            self._flow_handlers[intent_name] = _LazyFlowHandlerProxy(
                self,
                plugin.metadata.name,
                handler,
                intent_name=intent_name,
            )
            self._flow_handler_owners[intent_name] = plugin.metadata.name
            logger.debug(
                f"Registered flow handler: {intent_name} from {plugin.metadata.name}"
            )

    def _register_static_assets(self, plugin: "Plugin") -> None:
        """Register static assets directory from plugin."""
        static_path = plugin.get_static_assets_path()
        if static_path and static_path.exists():
            self._static_paths[plugin.metadata.name] = static_path
            self._static_path_owners[plugin.metadata.name] = plugin.metadata.name
            logger.debug(
                f"Registered static assets: {static_path} from {plugin.metadata.name}"
            )

    def _register_ui_tabs(self, plugin: "Plugin") -> None:
        """Register UI tabs from plugin."""
        tabs = plugin.get_ui_tabs()
        if tabs:
            self._ui_tabs[plugin.metadata.name] = tabs
            self._ui_tab_owners[plugin.metadata.name] = plugin.metadata.name
            logger.debug(f"Registered {len(tabs)} UI tabs from {plugin.metadata.name}")

    def register_all_components(self, plugin: "Plugin") -> None:
        """Register all components from a plugin."""
        self._register_agents(plugin)
        self._register_routers(plugin)
        self._register_entity_types(plugin)
        self._register_relationship_types(plugin)
        self._register_intent_patterns(plugin)
        self._register_flow_handlers(plugin)
        self._register_static_assets(plugin)
        self._register_ui_tabs(plugin)

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
        for static_name, owner in list(self._static_path_owners.items()):
            if owner == plugin_name:
                self._static_path_owners.pop(static_name, None)
                self._static_paths.pop(static_name, None)

        # Remove UI tabs
        for tab_name, owner in list(self._ui_tab_owners.items()):
            if owner == plugin_name:
                self._ui_tab_owners.pop(tab_name, None)
                self._ui_tabs.pop(tab_name, None)

        for entity_name, owner in list(self._entity_type_owners.items()):
            if owner == plugin_name:
                self._entity_type_owners.pop(entity_name, None)
                self._entity_types.pop(entity_name, None)

        for relationship_name, owner in list(self._relationship_type_owners.items()):
            if owner == plugin_name:
                self._relationship_type_owners.pop(relationship_name, None)
                self._relationship_types.pop(relationship_name, None)

        for intent_name, owner in list(self._intent_pattern_owners.items()):
            if owner == plugin_name:
                self._intent_pattern_owners.pop(intent_name, None)
                self._intent_patterns.pop(intent_name, None)

        for intent_name, owner in list(self._flow_handler_owners.items()):
            if owner == plugin_name:
                self._flow_handler_owners.pop(intent_name, None)
                self._flow_handlers.pop(intent_name, None)

        # Note: Routers cannot be easily removed from FastAPI after registration
        # This would require application restart for full cleanup

        logger.debug(f"Cleaned up components for plugin: {plugin_name}")
