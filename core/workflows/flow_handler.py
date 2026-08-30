"""Orchestrator bridge for workflow graphs.

Exposes a :class:`~core.workflows.builder.WorkflowDefinition` behind the
standard ``FlowHandler`` protocol, so a declarative graph can be registered
for an intent exactly like any imperative handler::

    orchestrator.register_handler("report_pipeline", WorkflowFlowHandler(wf))

The bridge inherits the orchestration context's durable checkpoint
(``context["checkpoint"]``, present when checkpointing is enabled): every
node execution is then recorded and a resumed run replays completed nodes
instead of re-executing them.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger
from core.workflows.builder import WorkflowDefinition
from core.workflows.executor import ExecutionStatus, WorkflowExecutor

logger = get_logger(__name__)


class WorkflowFlowHandler:
    """FlowHandler that runs a workflow graph for its registered intent."""

    def __init__(
        self,
        workflow: WorkflowDefinition,
        executor: WorkflowExecutor | None = None,
    ) -> None:
        """
        Args:
            workflow: The graph to execute for each request.
            executor: Optional pre-configured executor (agents/tools
                registries, ``max_steps``); a default one otherwise.
        """
        self._workflow = workflow
        self._executor = executor or WorkflowExecutor()

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """Run the workflow with ``query`` as initial input.

        Returns the orchestrator result shape: ``response`` carries the
        graph's final output, ``metadata`` its execution summary; a failed
        run comes back as a structured error result, not an exception.
        """
        result = await self._executor.execute(
            self._workflow,
            initial_input=query,
            checkpoint=context.get("checkpoint"),
        )
        metadata = {
            "workflow": self._workflow.name,
            "workflow_id": result.workflow_id,
            "nodes_executed": len(result.node_results),
            "duration_ms": result.duration_ms,
        }
        if result.status is not ExecutionStatus.COMPLETED:
            logger.warning(
                "workflow_flow_handler_failed",
                extra={"workflow": self._workflow.name, "error": result.error},
            )
            return {
                "response": (
                    f"Workflow '{self._workflow.name}' failed: {result.error}"
                ),
                "error": True,
                "metadata": metadata,
            }
        return {"response": result.output, "metadata": metadata}


__all__ = ["WorkflowFlowHandler"]
