"""
Reasoning Agent implementation.

Wraps the Tree of Thoughts engine and provides a high-level solve interface.
"""

from typing import Any, Dict
from core.reasoning.tot import TreeOfThoughtsAsync
from core.services.llm.service import LLMService


class ReasoningAgent:
    """
    Agent that uses the Tree of Thoughts engine to solve complex problems.
    """

    name = "reasoning-agent"

    def __init__(self, service: Any = None, sandbox_service: Any = None):
        """
        Initialize the Reasoning Agent.

        Args:
            service: A service provider, expected to be or contain an LLMService.
            sandbox_service: Optional SandboxService for code execution.
        """
        # If the service passed is the LLMService itself, use it.
        # Otherwise, try to resolve it.
        self.llm_service = None
        if isinstance(service, LLMService):
            self.llm_service = service
        elif hasattr(service, "generate_response"):
            self.llm_service = service

        if not self.llm_service and hasattr(service, "llm_service"):
            self.llm_service = service.llm_service

        if not self.llm_service:
            # Fallback for demo purposes if not injected
            self.llm_service = LLMService()

        # Update to Async Engine
        self.tot_engine = TreeOfThoughtsAsync(llm_service=self.llm_service)
        self.sandbox_service = sandbox_service

    async def solve(
        self, problem_description: str, max_steps: int = 5, branching_factor: int = 3
    ) -> Dict[str, Any]:
        """
        Solve a problem using Tree of Thoughts.

        Args:
            problem_description: The problem to solve.
            max_steps: Maximum height of the tree.
            branching_factor: Number of thoughts to generate at each step.

        Returns:
            Dictionary containing solution and tree visualization data.
        """
        # Pass sandbox as a tool if available
        tools = [self.sandbox_service] if self.sandbox_service else []

        result_dict = await self.tot_engine.solve(
            problem=problem_description,
            k=branching_factor,
            max_steps=max_steps,
            tools=tools,
        )
        return result_dict

    async def handle_request(self, query: str) -> Dict[str, Any]:
        """
        Handle a reasoning request via the standard agent interface.

        Args:
            query: The problem description or question.

        Returns:
            Dict[str, Any]: Solution found by the reasoning engine.
        """
        return await self.solve(query)
