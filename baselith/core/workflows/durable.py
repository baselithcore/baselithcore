"""Durable node visits for the workflow executor.

Split from :mod:`baselith.core.workflows.executor` (module size cap). A node
visit under a checkpoint is one recorded step — a resumed run replays the
node's output without re-executing it — and the work inside the visit gets its
own child scope of the run's checkpoint (see
:mod:`baselith.core.orchestration.checkpoint_scope`). AGENT nodes hand that
scope to the agent, crew or colony they run, so a node interrupted half-way
resumes from its own recorded calls instead of from scratch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .builder import WorkflowNode
    from .executor import ExecutionContext, NodeHandler, WorkflowExecutor

__all__ = ["NODE_CHECKPOINT_KEY", "run_durable_node"]

#: ``ExecutionContext.__dict__`` key holding the running visit's scope.
NODE_CHECKPOINT_KEY = "_node_checkpoint"


async def run_durable_node(
    executor: WorkflowExecutor,
    checkpoint: Any,
    handler: NodeHandler,
    node: WorkflowNode,
    context: ExecutionContext,
) -> Any:
    """Run one node visit (all its attempts) as a checkpoint step.

    One step per visit: the n-th visit of a node is its n-th occurrence,
    however many retries the run needed. The visit's scope is exposed as
    ``context.__dict__[NODE_CHECKPOINT_KEY]`` and bound as the ambient
    checkpoint while the handler runs. Durable mode executes branches
    sequentially, so the context slot is never shared.
    """
    from baselith.core.orchestration.checkpoint_scope import bind_checkpoint

    name = f"workflow:{node.id}"
    args = {"node_id": node.id, "node_type": node.type.value}
    occurrence = checkpoint.next_occurrence(name, args)
    make_scope = getattr(checkpoint, "scope", None)
    node_scope = (
        make_scope("workflow_node", {"node_id": node.id, "visit": occurrence})
        if make_scope is not None
        else None
    )

    async def _attempts() -> Any:
        context.__dict__[NODE_CHECKPOINT_KEY] = node_scope
        try:
            with bind_checkpoint(node_scope):
                return await executor._run_node_attempts(handler, node, context)
        finally:
            context.__dict__.pop(NODE_CHECKPOINT_KEY, None)

    return await checkpoint.run_step(
        name, args, _attempts, category="workflow_node", occurrence=occurrence
    )
