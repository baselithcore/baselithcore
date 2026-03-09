"""
Reasoning Handler.

Orchestrates complex logical reasoning using Tree-of-Thought flows.
"""

from core.observability.logging import get_logger
from typing import Any, Dict
from core.orchestration.handlers import BaseFlowHandler
from core.services.llm import get_llm_service
from core.reasoning.tot.engine import TreeOfThoughtsAsync

logger = get_logger(__name__)


class ReasoningHandler(BaseFlowHandler):
    """
    Handler for 'complex_reasoning' intent using Tree of Thoughts.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.llm_service = get_llm_service()
        self.tot_engine = TreeOfThoughtsAsync(llm_service=self.llm_service)

    async def handle(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process complex logical tasks using Tree of Thoughts.

        Runs a systematic search (BFS/DFS) across multiple lines of reasoning
        derived from the LLM service to solve the requested problem.

        Args:
            query: The complex logical problem description.
            context: Context containing search params like 'k', 'max_steps',
                    and 'strategy'.

        Returns:
            Dict[str, Any]: Solution result with reasoning steps and tree data.
        """
        try:
            logger.info(f"Starting reasoning for query: {query}")

            # Extract parameters from context or defaults
            # Allow user to influence depth/breadth via context if needed
            k = context.get("k", 3)
            max_steps = context.get("max_steps", 3)
            strategy = context.get("strategy", "bfs")

            result = await self.tot_engine.solve(
                problem=query, k=k, max_steps=max_steps, strategy=strategy
            )

            solution = result.get("solution", "No solution found.")
            steps = result.get("steps", [])

            return {
                "response": solution,
                "steps": steps,
                "tree_data": result.get("tree_data"),
                "metadata": {"reasoning_steps": len(steps), "strategy": strategy},
            }

        except Exception as e:
            logger.error(f"Error in Reasoning Handler: {e}")
            return {
                "response": "Sorry, an error occurred during reasoning.",
                "error": True,
                "metadata": {"error": str(e)},
            }
