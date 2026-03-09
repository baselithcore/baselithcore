"""Example plugin demonstrating the plugin system."""

from typing import Any, Dict, List
from fastapi import APIRouter

from core.plugins import AgentPlugin, RouterPlugin, GraphPlugin

from .agent import ExampleAgent
from .router import create_router
from .handlers import ExampleFlowHandler


class ExamplePlugin(AgentPlugin, RouterPlugin, GraphPlugin):
    """
    Example plugin demonstrating all plugin capabilities.

    This plugin shows how to:
    - Provide a custom agent
    - Add API routes
    - Register graph entity types
    - Register graph relationship types
    - Handle intent patterns
    - Provide Flow Handlers
    """

    def create_agent(self, service: Any, **kwargs) -> ExampleAgent:
        """Create example agent instance."""
        return ExampleAgent(service)

    def create_router(self) -> APIRouter:
        """Create example API router."""
        return create_router(self)

    def get_flow_handlers(self) -> Dict[str, Any]:
        """Return flow handlers."""
        handler = ExampleFlowHandler()
        return {
            "example_greeting": handler.handle_greeting,
            "example_complex": handler.handle_complex_task,
        }

    def register_entity_types(self) -> List[Dict[str, Any]]:
        """Register example entity types."""
        return [
            {
                "type": "example_task",
                "display_name": "Example Task",
                "schema": {
                    "title": str,
                    "description": str,
                    "status": str,
                    "priority": str,
                },
                "icon": "📝",
            },
            {
                "type": "example_note",
                "display_name": "Example Note",
                "schema": {
                    "content": str,
                    "tags": list,
                },
                "icon": "📄",
            },
        ]

    def register_relationship_types(self) -> List[Dict[str, Any]]:
        """Register example relationship types."""
        return [
            {
                "type": "EXAMPLE_DEPENDS_ON",
                "source_types": ["example_task"],
                "target_types": ["example_task"],
                "properties_schema": {
                    "reason": str,
                },
                "bidirectional": False,
            },
            {
                "type": "EXAMPLE_RELATES_TO",
                "source_types": ["example_task", "example_note"],
                "target_types": ["example_task", "example_note"],
                "properties_schema": {},
                "bidirectional": True,
            },
        ]

    def get_intent_patterns(self) -> List[Dict[str, Any]]:
        """Register example intent patterns."""
        return [
            {
                "name": "example_hello",
                "patterns": ["hello example", "example hello", "greet example"],
                "handler": "handle_hello",
                "priority": 1,
            },
            {
                "name": "example_help",
                "patterns": ["example help", "help example"],
                "handler": "handle_help",
                "priority": 1,
            },
        ]

    async def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize the plugin."""
        await super().initialize(config)

        # Initialize persistence
        from .persistence import ensure_schema, init_pool

        await init_pool()
        await ensure_schema()

        # Custom initialization logic here
        print(f"✅ Example plugin initialized with config: {config}")

    async def shutdown(self) -> None:
        """Shutdown the plugin."""
        print("👋 Example plugin shutting down")

        # Close persistence
        from .persistence import close_pool

        await close_pool()

        await super().shutdown()

    def get_static_paths(self) -> List[str]:
        """Return list of static directories to mount."""
        return ["static"]

    def get_scripts(self) -> List[str]:
        """Return list of scripts to inject."""
        return ["example-widget.js"]

    def get_stylesheets(self) -> List[str]:
        """Return list of stylesheets to inject."""
        return ["example-styles.css"]
