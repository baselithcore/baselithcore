"""Agent plugin interface for plugins that provide agents."""

from typing import Any, Dict, List
from abc import abstractmethod

from .interface import Plugin


class AgentPlugin(Plugin):
    """
    Plugin that provides agents to the baselith-core.

    Agent plugins can register custom agents that handle specific
    types of requests or intents.
    """

    @abstractmethod
    def create_agent(self, service: Any, **kwargs) -> Any:
        """
        Factory method to create an agent instance.

        Args:
            service: ChatService or similar service instance
            **kwargs: Additional arguments for agent creation

        Returns:
            Agent instance
        """
        pass

    def get_agents(self) -> List[Any]:
        """
        Return list of agents provided by this plugin.

        By default, returns a single agent created by create_agent.
        Override this method if the plugin provides multiple agents.

        Returns:
            List of agent instances
        """
        # Note: This requires the service to be passed during plugin initialization
        # or we need a different pattern. For now, return empty list and let
        # the orchestrator call create_agent when needed.
        return []

    def get_intent_patterns(self) -> List[Dict[str, Any]]:
        """
        Return intent patterns this agent handles.

        Intent patterns are used by the orchestrator to route requests
        to the appropriate agent.

        Returns:
            List of intent pattern dictionaries with keys:
                - name: Intent name
                - patterns: List of text patterns to match
                - handler: Handler method name (optional)
                - priority: Priority for intent matching (optional)
        """
        return []

    def get_agent_config(self) -> Dict[str, Any]:
        """
        Get agent-specific configuration.

        Returns:
            Dictionary of agent configuration
        """
        return self.get_config("agent", {})
