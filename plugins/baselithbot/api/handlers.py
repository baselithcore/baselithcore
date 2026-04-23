"""Flow handlers for the Baselithbot plugin.

The orchestrator dispatches user intents matched by ``get_intent_patterns``
into these handler coroutines. ``BaselithbotFlowHandler`` is the bridge
between intent matching and the underlying ``BaselithbotAgent`` instance
managed by ``BaselithbotPlugin``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from plugins.baselithbot.models import BaselithbotTask

if TYPE_CHECKING:
    from plugins.baselithbot.plugin import BaselithbotPlugin

logger = get_logger(__name__)


class BaselithbotFlowHandler:
    """Bridges orchestrator intents to ``BaselithbotAgent.execute`` calls."""

    def __init__(self, plugin: BaselithbotPlugin) -> None:
        self._plugin = plugin

    async def handle_browse(
        self, query: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run an autonomous browse task derived from a user query.

        Args:
            query: Natural-language goal extracted from the user message.
            context: Optional orchestration context. Recognized keys:
                - ``start_url`` (str): URL to land on before reasoning.
                - ``max_steps`` (int): Override default step budget.
                - ``extract_fields`` (list[str]): Fields to capture.

        Returns:
            Dict matching the orchestrator's flow-response contract.
        """
        ctx = context or {}
        agent = await self._plugin.get_or_start_agent()

        task = BaselithbotTask(
            goal=query,
            start_url=ctx.get("start_url"),
            max_steps=int(ctx.get("max_steps", agent.max_steps)),
            extract_fields=list(ctx.get("extract_fields", []) or []),
        )

        logger.info(
            "baselithbot_flow_handle_browse",
            goal_preview=query[:120],
            start_url=task.start_url,
            max_steps=task.max_steps,
        )

        result = await agent.execute(task)

        return {
            "status": "success" if result.success else "failed",
            "response": (
                f"Completed in {result.steps_taken} steps at {result.final_url}."
                if result.success
                else f"Browse failed: {result.error}"
            ),
            "data": {
                "final_url": result.final_url,
                "steps_taken": result.steps_taken,
                "extracted_data": result.extracted_data,
                "history": result.history,
                "error": result.error,
            },
        }


__all__ = ["BaselithbotFlowHandler"]
