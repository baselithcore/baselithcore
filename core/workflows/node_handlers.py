"""Default node handlers for the workflow executor.

Extracted from ``executor.py`` for the module size cap. Handlers that need
the executor (nested execution, agent/tool registries) take it as the first
argument and are bound with ``functools.partial`` at registration time.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

from .builder import WorkflowDefinition, WorkflowNode
from .conditions import _safe_condition

if TYPE_CHECKING:
    from .executor import ExecutionContext, WorkflowExecutor

logger = get_logger(__name__)


def handle_start(node: WorkflowNode, context: ExecutionContext) -> Any:
    """Handle start node."""
    return context.get_variable("input")


def handle_end(node: WorkflowNode, context: ExecutionContext) -> Any:
    """Handle end node."""
    return context.get_last_output()


def handle_transform(node: WorkflowNode, context: ExecutionContext) -> Any:
    """Handle transform node (``config['transform']`` callable or pass-through)."""
    transform_fn = node.config.get("transform")
    input_data = context.get_last_output()
    if callable(transform_fn):
        return transform_fn(input_data)
    return input_data


def handle_condition(node: WorkflowNode, context: ExecutionContext) -> bool:
    """Handle condition node using the safe expression evaluator."""
    expression = node.condition_expression
    if not expression:
        return True
    try:
        output = context.get_last_output()
        local_vars = {
            "output": output,
            "input": context.get_variable("input"),
        }
        local_vars.update(context.variables)
        return _safe_condition(expression, local_vars)
    except Exception as e:
        logger.warning(f"Condition check failed: {e}")
        return False


def handle_merge(node: WorkflowNode, context: ExecutionContext) -> Any:
    """Handle merge node: collect the outputs of its incoming branches."""
    workflow: WorkflowDefinition | None = context.__dict__.get("_workflow")
    if workflow is None:  # pragma: no cover - execute() always sets it
        return context.get_last_output()
    return [
        context.node_results[edge.source_id].output
        for edge in workflow.get_incoming_edges(node.id)
        if edge.source_id in context.node_results
    ]


async def handle_subgraph(
    executor: WorkflowExecutor, node: WorkflowNode, context: ExecutionContext
) -> Any:
    """Handle subgraph node: run the nested workflow to completion.

    The nested run gets its own context and ``max_steps`` budget; its final
    output becomes this node's output, and a failed nested run fails this
    node.
    """
    from .executor import ExecutionStatus

    sub = node.config.get("workflow")
    if isinstance(sub, dict):
        sub = WorkflowDefinition.from_dict(sub)
    if not isinstance(sub, WorkflowDefinition):
        raise ValueError(
            f"Subgraph node {node.id!r} has no workflow in config['workflow']"
        )
    result = await executor.execute(sub, initial_input=context.get_last_output())
    if result.status != ExecutionStatus.COMPLETED:
        raise RuntimeError(
            f"Subgraph {sub.name!r} failed: {result.error or result.status}"
        )
    return result.output


async def handle_agent(
    executor: WorkflowExecutor, node: WorkflowNode, context: ExecutionContext
) -> Any:
    """Handle agent node: run a registered (or inline) agent.

    Resolution order: ``config['agent']`` (an object with ``async
    run(prompt)``, e.g. :class:`core.agent.Agent`), then ``agent_id`` in the
    executor's ``agents`` registry. The prompt is ``config['prompt']`` with
    ``{input}`` replaced by the last output, or the last output itself.
    """
    agent = node.config.get("agent") or executor._agents.get(node.agent_id or "")
    if agent is None:
        raise ValueError(
            f"Agent node {node.id!r}: no agent registered for "
            f"agent_id={node.agent_id!r} and no config['agent']"
        )
    last = context.get_last_output()
    template = node.config.get("prompt")
    prompt = template.replace("{input}", str(last)) if template else str(last or "")
    result = await agent.run(prompt)
    return getattr(result, "output", result)


async def handle_tool(
    executor: WorkflowExecutor, node: WorkflowNode, context: ExecutionContext
) -> Any:
    """Handle tool node: call a registered (or inline) callable.

    Resolution order: ``config['fn']``, then ``tool_id`` in the executor's
    ``tools`` registry. Called with the last output; sync and async
    callables both work.
    """
    fn = node.config.get("fn") or executor._tools.get(node.tool_id or "")
    if fn is None:
        raise ValueError(
            f"Tool node {node.id!r}: no tool registered for "
            f"tool_id={node.tool_id!r} and no config['fn']"
        )
    result = fn(context.get_last_output())
    if asyncio.iscoroutine(result):
        return await result
    return result


async def handle_human(node: WorkflowNode, context: ExecutionContext) -> Any:
    """Handle a HUMAN approval-gate node — durable pause, fail-closed.

    With a durable checkpoint on the context, a fresh gate persists
    ``awaiting_approval`` and raises ``ApprovalPendingError`` (the same pause
    contract the ReAct autonomy gate uses, so the ``/approvals`` API drives
    both). A recorded approval lets the gate pass the last output through; a
    denial fails the run. Without a checkpoint the gate fails closed — a
    human gate must never silently wave traffic through.
    """
    from core.orchestration.autonomy import ApprovalPendingError

    category = str(node.config.get("category", "human_gate"))
    checkpoint = context.__dict__.get("_checkpoint")
    if checkpoint is None:
        raise RuntimeError(
            f"HUMAN node {node.id!r} requires durable checkpointing "
            "(execute the workflow with a checkpoint, e.g. via the "
            "orchestrator with ORCHESTRATOR_CHECKPOINT_ENABLED)."
        )
    decision = checkpoint.approval_decision(node.id, category)
    if decision is True:
        return context.get_last_output()
    if decision is False:
        raise RuntimeError(f"HUMAN gate {node.id!r} denied by reviewer")
    await checkpoint.await_approval(node.id, category)
    raise ApprovalPendingError(node.id, category, checkpoint.run_id)


def handle_loop(node: WorkflowNode, context: ExecutionContext) -> Any:
    """LOOP nodes fail closed — cycles are modeled with CONDITION edges.

    Before this handler existed, a LOOP node silently passed the last output
    through (the same hole HUMAN nodes had). The executor's iterative
    traversal already runs cycles correctly when a CONDITION edge points back
    to an earlier node, bounded by ``max_steps`` — so LOOP has no distinct
    semantics to implement, and pretending to execute one would hide a
    mis-modeled graph.
    """
    raise RuntimeError(
        f"LOOP node {node.id!r} is not supported: model the cycle with a "
        "CONDITION edge pointing back to an earlier node (bounded by the "
        "executor's max_steps)."
    )


__all__ = [
    "handle_agent",
    "handle_condition",
    "handle_end",
    "handle_human",
    "handle_loop",
    "handle_merge",
    "handle_start",
    "handle_subgraph",
    "handle_tool",
    "handle_transform",
]
