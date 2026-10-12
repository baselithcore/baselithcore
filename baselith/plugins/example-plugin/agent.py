"""Example Agent module."""

from typing import Any


class ExampleAgent:
    """
    Simple example agent.

    This agent demonstrates how to create a custom agent
    that can be registered through the plugin system.
    """

    name = "example-agent"

    def __init__(self, service: Any = None):
        """
        Initialize the agent.

        Args:
            service: Service dependency (e.g., for LLM or other plugins)
        """
        self.service = service

    async def handle_request(self, query: str) -> str:
        """
        Handle a request.

        Args:
            query: User query

        Returns:
            Response string
        """
        # In a real agent, you might use self.service.chat(query)
        return f"Example agent received: {query}"
