"""
MyPlugin - Plugin Template.

Copy this file and rename 'MyPlugin' to match your plugin's purpose.
Replace all 'MyPlugin'/'my-plugin' references throughout.
"""

from typing import Any, Dict, List, Optional
from core.observability.logging import get_logger

from core.plugins.agent_plugin import AgentPlugin
from core.plugins.interface import PluginMetadata

logger = get_logger(__name__)


class MyPlugin(AgentPlugin):
    """
    Template for a BaselithCore Agent Plugin.
    
    This follows the Sacred Core principle: logic here is about 
    orchestration and exposing capabilities, not core infrastructure.
    """

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="my-plugin",          # slug for the plugin
            version="1.0.0",
            description="A custom agent plugin for BaselithCore.",
            author="Your Name/Team",
            tags=["agent", "custom"]
        )

    def __init__(self) -> None:
        super().__init__()
        self._config: Dict[str, Any] = {}

    async def initialize(self, config: Dict[str, Any]) -> None:
        """
        Dogma III: Async Everything.
        Called when the plugin is loaded by the framework.
        """
        self._config = config
        logger.info(f"🔌 Plugin {self.metadata.name} initialized")

    async def shutdown(self) -> None:
        """Lifecycle Sovereignty: clean up resources."""
        logger.info(f"🔌 Plugin {self.metadata.name} shutting down")

    def create_agent(self, **kwargs) -> Optional[Any]:
        """
        Dogma II: DI First.
        Factory method to create the agent. Dependencies should be 
        resolved via the DependencyContainer.
        """
        # Example pattern:
        # from .agent import MyAgent
        # return MyAgent(agent_id=f"{self.metadata.name}-agent", config=self._config)
        return None

    def get_agents(self) -> List[Any]:
        """Return list of agents provided by this plugin."""
        agent = self.create_agent()
        return [agent] if agent else []


# Required for the plugin loader to find the entry point
plugin = MyPlugin()
