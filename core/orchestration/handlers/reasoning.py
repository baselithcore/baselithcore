"""
Reasoning Handler.

Orchestrates complex logical reasoning using Tree-of-Thought flows.
"""

from typing import Any

from core.observability.logging import get_logger
from core.orchestration.enforcement import enforce_iteration
from core.orchestration.handlers import BaseFlowHandler
from core.reasoning.tot.engine import TreeOfThoughtsAsync
from core.services.llm import get_llm_service

logger = get_logger(__name__)


class ReasoningHandler(BaseFlowHandler):
    """
    Handler for 'complex_reasoning' intent using Tree of Thoughts.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._llm_service = None
        self._tot_engine = None

    @property
    def llm_service(self):
        """Lazy load the LLM service."""
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    @property
    def tot_engine(self):
        """Lazy load the ToT engine."""
        if self._tot_engine is None:
            self._tot_engine = TreeOfThoughtsAsync(llm_service=self.llm_service)
        return self._tot_engine

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        Process a complex logical task with a selectable reasoning strategy.

        ``context["strategy"]`` picks the engine:

        * ``"react"`` — explicit Thought/Action/Observation loop
          (:class:`~core.reasoning.react.ReActAgent`), when tools are supplied
          in ``context["react_tools"]``.
        * ``"parallel_tools"`` — concurrent execution of independent tool calls
          (:class:`~core.orchestration.parallel.ParallelToolExecutor`), from
          ``context["tool_calls"]`` + ``context["tool_registry"]``.
        * anything else (default ``"bfs"`` / ``"dfs"``) — Tree of Thoughts.

        Args:
            query: The complex logical problem description.
            context: Search/strategy params (``strategy``, ``k``, ``max_steps``,
                and the tool inputs for the react / parallel strategies).

        Returns:
            Dict[str, Any]: Solution result with reasoning steps and metadata.
        """
        try:
            logger.info(f"Starting reasoning for query: {query}")

            # Count this reasoning flow against the per-request loop budget
            # (fail-closed): raises BudgetExceededError when the iteration cap
            # is reached, caught below as a graceful error response.
            enforce_iteration(context)

            strategy = context.get("strategy", "bfs")
            if strategy == "react" and context.get("react_tools"):
                return await self._run_react(query, context)
            if strategy == "parallel_tools" and context.get("tool_calls"):
                return await self._run_parallel_tools(query, context)
            return await self._run_tot(query, context)

        except Exception as e:
            logger.error(f"Error in Reasoning Handler: {e}")
            return {
                "response": "Sorry, an error occurred during reasoning.",
                "error": True,
                "metadata": {"error": str(e)},
            }

    async def _run_tot(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """Tree-of-Thoughts search (the default strategy)."""
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

    async def _run_react(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """Run the ReAct loop over the tools provided on the context.

        ``context["react_tools"]`` is a list of
        :class:`~core.reasoning.react.ToolDefinition`.
        """
        from core.reasoning.react import ReActAgent

        # Bounded tool execution in the orchestrated path: a hung tool must
        # not hang the whole request. Overridable per-request via context.
        agent = ReActAgent(
            tools=context["react_tools"],
            max_iterations=context.get("max_iterations", 5),
            llm_service=self.llm_service,
            tool_timeout=context.get("tool_timeout", 120.0),
            tool_retries=context.get("tool_retries", 1),
        )
        result = await agent.run(query)
        return {
            "response": result.final_answer,
            "steps": [str(step) for step in result.trace],
            "metadata": {
                "strategy": "react",
                "iterations": result.iterations_used,
                "hit_limit": result.hit_limit,
            },
        }

    async def _run_parallel_tools(
        self, query: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute independent tool calls concurrently.

        ``context["tool_calls"]`` is a list of
        :class:`~core.orchestration.parallel.ToolCall`; ``context["tool_registry"]``
        maps tool names to callables. The executor is wired with the per-request
        autonomy policy, budget, and contract from the context, so the same
        gating and caps apply as in the main loop.
        """
        from core.orchestration.parallel import ParallelToolExecutor

        executor = ParallelToolExecutor(
            autonomy_policy=context.get("autonomy_policy"),
            human_intervention=context.get("human_intervention"),
            loop_budget=context.get("loop_budget"),
            contract_validator=context.get("contract_validator"),
        )
        for name, fn in (context.get("tool_registry") or {}).items():
            executor.register_tool(name, fn)

        results = await executor.execute_parallel(context["tool_calls"])
        outputs = {
            r.call_id: (r.result if r.success else f"ERROR: {r.error}") for r in results
        }
        return {
            "response": outputs,
            "steps": [
                f"{r.tool_name}({r.call_id}): "
                f"{'ok' if r.success else 'failed'} in {r.execution_time_ms:.1f}ms"
                for r in results
            ],
            "metadata": {
                "strategy": "parallel_tools",
                "tool_count": len(results),
                "success": all(r.success for r in results),
            },
        }
