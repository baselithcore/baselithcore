"""
Agent implementation for ${{ values.pluginName }}.

Dogma II — DI First: heavy dependencies are injected via __init__() config,
             not imported at module level, to keep the agent testable in
             isolation.
Dogma III— Async Everything: process() is async.
"""

from typing import Any, Dict

{%- set agentClass = values.pluginName | replace("-", " ") | title | replace(" ", "") + "Agent" %}


class {{ agentClass }}:
    """${{ values.description }}"""

    #: Name used by the Orchestrator to dispatch intents.
    name: str = "${{ values.pluginName }}-agent"

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config

    async def process(self, query: str, **kwargs: Any) -> str:
        """
        Process a query and return a response.

        Args:
            query: The user input or intent payload.

        Returns:
            A string response or JSON-serialisable value.
        """
        # TODO: Implement agent logic.
        raise NotImplementedError(
            f"{self.__class__.__name__}.process() not yet implemented"
        )
