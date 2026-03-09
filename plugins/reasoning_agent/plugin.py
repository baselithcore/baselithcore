"""
Reasoning Agent Plugin.

Exposes the Tree of Thoughts engine to the BaselithCore orchestration layer.
"""

from typing import Any, Dict, List
from core.plugins import AgentPlugin
from .reasoning_agent import ReasoningAgent


class ReasoningAgentPlugin(AgentPlugin):
    """
    Plugin that exposes the Reasoning Agent (Tree of Thoughts).
    """

    def get_intent_patterns(self) -> List[tuple[str, str, float]]:
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

    def get_flow_handlers(self) -> Dict[str, Any]:
        """Return flow handler for reasoning intent."""
        return {
            "reasoning": ReasoningFlowHandler(
                self.create_agent(None)
            )  # Helper to create agent lazy or we need access to service
        }

    async def initialize(self, config: Dict[str, Any]) -> None:
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

    def __init__(self, agent: ReasoningAgent):
        """
        Initialize flow handler.

        Args:
            agent: The ReasoningAgent instance.
        """
        self.agent = agent

    async def handle(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Handle reasoning request."""
        # Extract params from context if available, otherwise defaults
        max_steps = context.get("max_steps", 5)
        branching_factor = context.get("branching_factor", 3)

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
