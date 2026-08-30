"""
Workflow Executor

Execute workflow definitions step by step.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from core.observability.logging import get_logger

from .builder import NodeType, WorkflowDefinition, WorkflowEdge, WorkflowNode
from .conditions import (  # noqa: F401  (re-export: tests and callers import from here)
    _ast_interpret,
    _safe_condition,
)

logger = get_logger(__name__)


class ExecutionStatus(str, Enum):
    """Status of workflow execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class NodeResult:
    """Result of executing a single node."""

    node_id: str
    status: ExecutionStatus
    output: Any = None
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class ExecutionContext:
    """Context passed through workflow execution."""

    workflow_id: str
    variables: dict[str, Any] = field(default_factory=dict)
    node_results: dict[str, NodeResult] = field(default_factory=dict)

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def set_variable(self, name: str, value: Any) -> None:
        """Set a context variable."""
        self.variables[name] = value

    def get_variable(self, name: str, default: Any = None) -> Any:
        """Get a context variable."""
        return self.variables.get(name, default)

    def get_last_output(self) -> Any:
        """Get the output of the last executed node."""
        if not self.node_results:
            return None
        last_result = list(self.node_results.values())[-1]
        return last_result.output


@dataclass
class WorkflowResult:
    """Result of complete workflow execution."""

    workflow_id: str
    status: ExecutionStatus
    output: Any = None
    error: str | None = None
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @property
    def duration_ms(self) -> float:
        """Calculate total execution duration."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds() * 1000
        return 0.0


# Type for node handlers
NodeHandler = Callable[[WorkflowNode, ExecutionContext], Any]


class WorkflowExecutor:
    """
    Execute workflow definitions.

    Handles node traversal, condition evaluation, parallel execution,
    per-node retry (``WorkflowNode.retries`` / ``retry_backoff``) and cyclic
    graphs. Traversal is **iterative**, so a cycle (e.g. a
    generate → evaluate → refine loop closed by a CONDITION edge back to an
    earlier node) executes correctly instead of recursing without bound; the
    ``max_steps`` budget makes a loop that never converges terminate as a
    failed run rather than hanging.
    """

    def __init__(
        self,
        max_steps: int = 1000,
        agents: dict[str, Any] | None = None,
        tools: dict[str, Callable[..., Any]] | None = None,
    ):
        """Initialize executor.

        Args:
            max_steps: Hard cap on total node executions per run (cycle
                guard). Revisits count; crossing the cap fails the run.
            agents: Registry resolving AGENT nodes' ``agent_id`` to an agent
                object exposing ``async run(prompt)`` (e.g.
                :class:`core.agent.Agent`).
            tools: Registry resolving TOOL nodes' ``tool_id`` to a callable
                (sync or async) invoked with the last output.
        """
        self.max_steps = max_steps
        self._agents = agents or {}
        self._tools = tools or {}
        self._handlers: dict[NodeType, NodeHandler] = {}
        self._setup_default_handlers()

    def _setup_default_handlers(self) -> None:
        """Setup default node type handlers (bodies in ``node_handlers``)."""
        from functools import partial

        from . import node_handlers as h

        self._handlers[NodeType.START] = h.handle_start
        self._handlers[NodeType.END] = h.handle_end
        self._handlers[NodeType.HUMAN] = h.handle_human
        self._handlers[NodeType.LOOP] = h.handle_loop
        self._handlers[NodeType.TRANSFORM] = h.handle_transform
        self._handlers[NodeType.CONDITION] = h.handle_condition
        self._handlers[NodeType.MERGE] = h.handle_merge
        self._handlers[NodeType.SUBGRAPH] = partial(h.handle_subgraph, self)
        self._handlers[NodeType.AGENT] = partial(h.handle_agent, self)
        self._handlers[NodeType.TOOL] = partial(h.handle_tool, self)

    def register_handler(self, node_type: NodeType, handler: NodeHandler) -> None:
        """
        Register a custom handler for a node type.

        Args:
            node_type: The node type to handle
            handler: Async function that processes the node
        """
        self._handlers[node_type] = handler

    async def execute(
        self,
        workflow: WorkflowDefinition,
        initial_input: Any = None,
        checkpoint: Any = None,
    ) -> WorkflowResult:
        """
        Execute a workflow.

        Args:
            workflow: The workflow to execute
            initial_input: Initial input data
            checkpoint: Optional
                :class:`~core.orchestration.checkpoint.CheckpointManager`.
                When given, every node execution is recorded through
                ``run_step`` under a deterministic replay key, so a resumed
                run replays completed nodes' outputs instead of re-executing
                them. Durable runs require node outputs to be
                JSON-serializable for the persistent store, and execute
                PARALLEL branches sequentially (deterministic replay cursors).

        Returns:
            WorkflowResult with execution details
        """
        # Validate first
        errors = workflow.validate()
        if errors:
            return WorkflowResult(
                workflow_id=workflow.id,
                status=ExecutionStatus.FAILED,
                error=f"Validation failed: {errors[0]}",
            )

        # Create execution context
        context = ExecutionContext(workflow_id=workflow.id)
        context.set_variable("input", initial_input)
        # Workflow reference for handlers that need the graph (e.g. MERGE
        # collecting its incoming branches). Private attr, same pattern as
        # the _steps_taken counter.
        context.__dict__["_workflow"] = workflow
        if checkpoint is not None:
            context.__dict__["_checkpoint"] = checkpoint

        result = WorkflowResult(
            workflow_id=workflow.id,
            status=ExecutionStatus.RUNNING,
            started_at=datetime.now(UTC),
        )

        # Lazy: core.workflows must stay importable without orchestration.
        from core.orchestration.autonomy import ApprovalPendingError

        try:
            # Find start node
            start_node = workflow.get_start_node()
            if not start_node:
                raise ValueError("No start node found")

            # Execute from start
            await self._run_chain(workflow, start_node, context)

            # Success
            result.status = ExecutionStatus.COMPLETED
            result.output = context.get_last_output()
            result.node_results = context.node_results

        except ApprovalPendingError:
            # Durable human-in-the-loop pause from a HUMAN gate — not a
            # failure. Propagate so the orchestrator surfaces the
            # awaiting-approval response with the run id.
            raise
        except Exception as e:
            logger.error(f"Workflow execution failed: {e}", exc_info=True)
            result.status = ExecutionStatus.FAILED
            result.error = str(e)
            result.node_results = context.node_results

        result.completed_at = datetime.now(UTC)
        return result

    async def _run_chain(
        self,
        workflow: WorkflowDefinition,
        node: WorkflowNode | None,
        context: ExecutionContext,
        halt_before_merge: bool = False,
    ) -> WorkflowNode | None:
        """Run a chain of nodes iteratively from *node* until END or no edge.

        Iterative (no recursion along the path) so cycles execute correctly;
        only PARALLEL fan-out awaits sub-chains. Every node execution counts
        against ``max_steps``.

        With ``halt_before_merge`` (parallel branches) the chain stops
        *before* executing a MERGE node and returns it, so the fan-in node
        runs exactly once — after all branches have completed — instead of
        once per branch (or, as before this flag existed, never).
        """
        while node is not None:
            if halt_before_merge and node.type == NodeType.MERGE:
                return node

            output = await self._execute_single_node(node, context)

            if node.type == NodeType.END:
                return None

            outgoing_edges = workflow.get_outgoing_edges(node.id)

            if node.type == NodeType.CONDITION:
                edge = self._pick_condition_edge(output, outgoing_edges)
                node = workflow.get_node(edge.target_id) if edge else None
            elif node.type == NodeType.PARALLEL:
                # Fan-out: each branch is its own iterative chain; continue
                # from the convergence MERGE node (None when branches ran to
                # END on their own).
                node = await self._execute_parallel(workflow, outgoing_edges, context)
            else:
                node = (
                    workflow.get_node(outgoing_edges[0].target_id)
                    if outgoing_edges
                    else None
                )
        return None

    async def _execute_single_node(
        self,
        node: WorkflowNode,
        context: ExecutionContext,
    ) -> Any:
        """Execute one node (with retry/backoff) and record its result.

        Raises after exhausting ``node.retries`` extra attempts; timeouts are
        not retried (a node that hit its deadline will likely hit it again).
        """
        steps = context.__dict__.get("_steps_taken", 0) + 1
        context.__dict__["_steps_taken"] = steps
        if steps > self.max_steps:
            raise RuntimeError(
                f"Workflow exceeded max_steps={self.max_steps} — "
                "likely a cycle that never converges."
            )

        start_time = time.perf_counter()
        handler = self._handlers.get(node.type)
        output = None
        error = None
        status = ExecutionStatus.COMPLETED

        try:
            if handler:
                checkpoint = context.__dict__.get("_checkpoint")
                if checkpoint is not None:
                    # Durable mode: the whole attempt sequence (timeout +
                    # retries) is one recorded step, so a resumed run replays
                    # the node's final output without re-executing it. One
                    # run_step per node visit keeps replay cursors aligned
                    # regardless of how many retries the original run needed.
                    output = await checkpoint.run_step(
                        f"workflow:{node.id}",
                        {"node_id": node.id, "node_type": node.type.value},
                        lambda: self._run_node_attempts(handler, node, context),
                        category="workflow_node",
                    )
                else:
                    output = await self._run_node_attempts(handler, node, context)
            else:
                # Default: pass through
                output = context.get_last_output()
                logger.warning(f"No handler for node type: {node.type}")

        except Exception as e:
            from core.orchestration.autonomy import ApprovalPendingError

            if isinstance(e, ApprovalPendingError):
                # Durable pause, not a node failure: no FAILED record, the
                # typed exception must reach the orchestrator intact.
                raise
            error = str(e)
            status = ExecutionStatus.FAILED

        duration = (time.perf_counter() - start_time) * 1000
        # Re-insert on revisit (cycles) so get_last_output tracks execution
        # order, not first-visit order.
        context.node_results.pop(node.id, None)
        context.node_results[node.id] = NodeResult(
            node_id=node.id,
            status=status,
            output=output,
            error=error,
            duration_ms=duration,
        )

        if status == ExecutionStatus.FAILED:
            raise Exception(error)
        return output

    async def _run_node_attempts(
        self, handler: NodeHandler, node: WorkflowNode, context: ExecutionContext
    ) -> Any:
        """Run one node's handler with its timeout and retry/backoff policy.

        Extracted from :meth:`_execute_single_node` so durable mode can wrap
        the whole attempt sequence in a single checkpoint step. Timeouts are
        not retried (a node that hit its deadline will likely hit it again).
        """
        for attempt in range(max(0, node.retries) + 1):
            try:
                if node.timeout:
                    try:
                        return await asyncio.wait_for(
                            self._invoke_handler(handler, node, context),
                            timeout=node.timeout,
                        )
                    except TimeoutError as err:
                        raise TimeoutError(
                            f"Node execution timed out after {node.timeout}s"
                        ) from err
                return await self._invoke_handler(handler, node, context)
            except TimeoutError:
                raise
            except Exception as exc:
                from core.orchestration.autonomy import ApprovalPendingError

                if isinstance(exc, ApprovalPendingError):
                    raise  # durable pause — never a retryable failure
                if attempt >= max(0, node.retries):
                    raise
                delay = node.retry_backoff * (2**attempt)
                logger.warning(
                    "workflow_node_retry node=%s attempt=%d/%d in %.1fs: %s",
                    node.id,
                    attempt + 1,
                    node.retries + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable: every attempt path returns or raises")

    def _pick_condition_edge(
        self,
        condition_result: Any,
        edges: list[WorkflowEdge],
    ) -> WorkflowEdge | None:
        """Pick the correct edge based on condition result."""
        for edge in edges:
            if edge.condition_label == "true" and condition_result:
                return edge
            if edge.condition_label == "false" and not condition_result:
                return edge
        # Default: first edge
        return edges[0] if edges else None

    async def _execute_parallel(
        self,
        workflow: WorkflowDefinition,
        edges: list[WorkflowEdge],
        context: ExecutionContext,
    ) -> WorkflowNode | None:
        """Execute branches in parallel; return their convergence MERGE node.

        Each branch halts before a MERGE node. All branches must converge on
        the same one (or on none — legacy graphs whose branches run to END);
        divergent merges are a graph error. The returned node is executed by
        the caller exactly once, after every branch has completed.
        """
        tasks = []
        for edge in edges:
            next_node = workflow.get_node(edge.target_id)
            if next_node:
                task = self._run_chain(
                    workflow, next_node, context, halt_before_merge=True
                )
                tasks.append(task)

        if not tasks:
            return None
        if context.__dict__.get("_checkpoint") is not None:
            # Durable mode runs branches sequentially (edge order): replay
            # cursors must assign the same key to the same node on every
            # pass, and concurrent per-step saves would interleave version
            # bumps in the store.
            halted = [await task for task in tasks]
        else:
            halted = await asyncio.gather(*tasks)
        merges = {n.id: n for n in halted if n is not None}
        if len(merges) > 1:
            raise RuntimeError(
                f"Parallel branches converge on different MERGE nodes: {sorted(merges)}"
            )
        return next(iter(merges.values()), None)

    async def _invoke_handler(
        self, handler: NodeHandler, node: WorkflowNode, context: ExecutionContext
    ) -> Any:
        """Invoke handler handling both sync and async."""
        result = handler(node, context)
        if asyncio.iscoroutine(result):
            return await result
        return result
