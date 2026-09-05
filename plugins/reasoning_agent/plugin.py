"""
Reasoning Agent Plugin.

Exposes the Tree of Thoughts engine to the BaselithCore orchestration layer.
"""

from collections.abc import Callable
from typing import Any

from core.plugins import AgentPlugin

from .reasoning_agent import ReasoningAgent


class ReasoningAgentPlugin(AgentPlugin):
    """
    Plugin that exposes the Reasoning Agent (Tree of Thoughts).
    """

    def get_intent_patterns(self) -> list[tuple[str, str, float]]:
        """
        Return intent recognition patterns for the orchestrator.

        Returns:
            List of (pattern, intent, confidence) tuples.
        """
        return [
            {
                "name": "reasoning",
                "patterns": [
                    "analizza",
                    "analyze",
                    "confronta",
                    "compare",
                    "pianifica",
                    "plan",
                    "reason",
                    "ragiona",
                    "risolvi",
                    "solve",
                    "step by step",
                ],
                "description": "Requests requiring complex reasoning, planning, or multi-step analysis.",
                "priority": 10,
            }
        ]

    def create_agent(self, service: Any, **kwargs) -> ReasoningAgent:
        """Create reasoning agent instance."""
        try:
            from core.services.sandbox.service import SandboxService

            sandbox = SandboxService()
        except ImportError:
            sandbox = None

        return ReasoningAgent(service, sandbox_service=sandbox)

    def get_flow_handlers(self) -> dict[str, Any]:
        """Return flow handler for reasoning intent."""
        return {
            "reasoning": ReasoningFlowHandler(
                self.create_agent(None), config_provider=self.get_config
            )  # Helper to create agent lazy or we need access to service
        }

    async def initialize(self, config: dict[str, Any]) -> None:
        """Initialize the plugin."""
        await super().initialize(config)
        # Store config for agent creation if needed
        self._config = config
        print("🧠 Reasoning Agent Plugin initialized.")

    async def shutdown(self) -> None:
        """Shutdown the plugin."""
        print("🧠 Reasoning Agent Plugin shutting down.")
        await super().shutdown()


class ReasoningFlowHandler:
    """Handles visual workflow execution for reasoning nodes."""

    def __init__(
        self,
        agent: ReasoningAgent,
        config_provider: Callable[[str, Any], Any] | None = None,
    ):
        """
        Initialize flow handler.

        Args:
            agent: The ReasoningAgent instance.
            config_provider: ``(key, default) -> value`` lookup into the plugin
                config (``configs/plugins.yaml`` ``reasoning_agent:`` entry).
                Request ``context`` keys still take precedence per call.
        """
        self.agent = agent
        self._config_provider = config_provider

    def _setting(self, key: str, default: Any) -> Any:
        if self._config_provider is None:
            return default
        try:
            value = self._config_provider(key, default)
        except Exception:
            return default
        return default if value is None else value

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """Handle reasoning request."""
        # Per-request context overrides the plugin config, which overrides defaults
        max_steps = context.get("max_steps", self._setting("max_steps", 5))
        branching_factor = context.get(
            "branching_factor", self._setting("branching_factor", 3)
        )

        result = await self.agent.solve(
            problem_description=query,
            max_steps=max_steps,
            branching_factor=branching_factor,
        )

        # Ensure result specific format if needed by orchestrator,
        # but generic dict is fine. we might want to standardize keys.
        return {
            "type": "reasoning_result",
            "content": result.get("best_solution", "No solution found."),
            "metadata": {
                "steps": result.get("steps", []),
                "tree_visualization": result.get("tree_visualization", ""),
            },
        }
