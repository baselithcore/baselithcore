"""
Workflow Executor

Execute workflow definitions step by step.
"""

import ast
import asyncio
from core.observability.logging import get_logger
import operator
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .builder import WorkflowDefinition, WorkflowNode, WorkflowEdge, NodeType

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Safe expression evaluator (replaces bare code execution)
# ---------------------------------------------------------------------------

_SAFE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.And: None,  # handled specially
    ast.Or: None,
}


def _safe_condition(expression: str, variables: Dict[str, Any]) -> bool:
    """Interpret a simple condition expression safely via AST.

    Supports: comparisons (==, !=, <, >, <=, >=, in, not in, is, is not),
    boolean operators (and, or, not), attribute access on provided variables,
    string/int/float/bool/None literals, and arithmetic (+, -, *).

    Raises ``ValueError`` for any unsupported AST node.
    """
    tree = ast.parse(expression.strip(), mode="eval")
    return bool(_ast_interpret(tree.body, variables))


def _ast_interpret(node: ast.AST, env: Dict[str, Any]) -> Any:
    """
    Evaluate an AST node representing an expression against a variable environment.

    Args:
        node: The parsed Python AST node to evaluate.
        env: A dictionary of variable names mapped to their current values.

    Returns:
        The evaluated result of the expression.
    """
    if isinstance(node, ast.Expression):
        return _ast_interpret(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError(f"Undefined variable: {node.id}")
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ValueError(
                f"Access to private/dunder attribute denied: {node.attr!r}"
            )
        obj = _ast_interpret(node.value, env)
        return getattr(obj, node.attr)
    if isinstance(node, ast.Subscript):
        obj = _ast_interpret(node.value, env)
        key = _ast_interpret(node.slice, env)
        return obj[key]
    if isinstance(node, ast.Compare):
        left = _ast_interpret(node.left, env)
        for op_node, comparator in zip(node.ops, node.comparators):
            right = _ast_interpret(comparator, env)
            op_fn = _SAFE_OPS.get(type(op_node))
            if op_fn is None:
                raise ValueError(f"Unsupported comparison: {type(op_node).__name__}")
            if not op_fn(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_ast_interpret(v, env) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(_ast_interpret(v, env) for v in node.values)
        raise ValueError(f"Unsupported boolean op: {type(node.op).__name__}")
    if isinstance(node, ast.UnaryOp):
        operand = _ast_interpret(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")
    if isinstance(node, ast.BinOp):
        left = _ast_interpret(node.left, env)
        right = _ast_interpret(node.right, env)
        op_fn = _SAFE_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported binary op: {type(node.op).__name__}")
        return op_fn(left, right)
    if isinstance(node, ast.Call):
        # Only allow a small whitelist of builtins
        if isinstance(node.func, ast.Name) and node.func.id in (
            "len",
            "str",
            "int",
            "float",
            "bool",
        ):
            from typing import cast, Callable

            fn = cast(
                Callable,
                {"len": len, "str": str, "int": int, "float": float, "bool": bool}[
                    node.func.id
                ],
            )
            args = [_ast_interpret(a, env) for a in node.args]
            return fn(*args)
        raise ValueError(f"Function calls not allowed: {ast.dump(node.func)}")
    if isinstance(node, ast.IfExp):
        if _ast_interpret(node.test, env):
            return _ast_interpret(node.body, env)
        return _ast_interpret(node.orelse, env)
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


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
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class ExecutionContext:
    """Context passed through workflow execution."""

    workflow_id: str
    variables: Dict[str, Any] = field(default_factory=dict)
    node_results: Dict[str, NodeResult] = field(default_factory=dict)

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

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
    error: Optional[str] = None
    node_results: Dict[str, NodeResult] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

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

    Handles node traversal, condition evaluation, and parallel execution.
    """

    def __init__(self):
        """Initialize executor."""
        self._handlers: Dict[NodeType, NodeHandler] = {}
        self._setup_default_handlers()

    def _setup_default_handlers(self) -> None:
        """Setup default node type handlers."""
        self._handlers[NodeType.START] = self._handle_start
        self._handlers[NodeType.END] = self._handle_end
        self._handlers[NodeType.TRANSFORM] = self._handle_transform
        self._handlers[NodeType.CONDITION] = self._handle_condition

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
    ) -> WorkflowResult:
        """
        Execute a workflow.

        Args:
            workflow: The workflow to execute
            initial_input: Initial input data

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

        result = WorkflowResult(
            workflow_id=workflow.id,
            status=ExecutionStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

        try:
            # Find start node
            start_node = workflow.get_start_node()
            if not start_node:
                raise ValueError("No start node found")

            # Execute from start
            await self._execute_node(workflow, start_node, context)

            # Success
            result.status = ExecutionStatus.COMPLETED
            result.output = context.get_last_output()
            result.node_results = context.node_results

        except Exception as e:
            logger.error(f"Workflow execution failed: {e}", exc_info=True)
            result.status = ExecutionStatus.FAILED
            result.error = str(e)
            result.node_results = context.node_results

        result.completed_at = datetime.now(timezone.utc)
        return result

    async def _execute_node(
        self,
        workflow: WorkflowDefinition,
        node: WorkflowNode,
        context: ExecutionContext,
    ) -> None:
        """Execute a single node and continue to next."""
        start_time = time.perf_counter()

        # Execute the node
        handler = self._handlers.get(node.type)
        output = None
        error = None
        status = ExecutionStatus.COMPLETED

        try:
            if handler:
                if node.timeout:
                    try:
                        result = await asyncio.wait_for(
                            self._invoke_handler(handler, node, context),
                            timeout=node.timeout,
                        )
                    except asyncio.TimeoutError as err:
                        raise TimeoutError(
                            f"Node execution timed out after {node.timeout}s"
                        ) from err
                else:
                    result = await self._invoke_handler(handler, node, context)
                output = result
            else:
                # Default: pass through
                output = context.get_last_output()
                logger.warning(f"No handler for node type: {node.type}")

        except Exception as e:
            error = str(e)
            status = ExecutionStatus.FAILED
            # Don't re-raise immediately so we can record the result
            # But we might want to stop the workflow?
            # For now, let's re-raise after recording to stop execution flow
            pass

        finally:
            duration = (time.perf_counter() - start_time) * 1000
            node_result = NodeResult(
                node_id=node.id,
                status=status,
                output=output,
                error=error,
                duration_ms=duration,
            )
            context.node_results[node.id] = node_result

            if status == ExecutionStatus.FAILED:
                raise Exception(error)

        # Stop if end node
        if node.type == NodeType.END:
            return

        # Find next nodes
        outgoing_edges = workflow.get_outgoing_edges(node.id)

        if node.type == NodeType.CONDITION:
            # Evaluate condition and pick branch
            edge = self._pick_condition_edge(output, outgoing_edges)
            if edge:
                next_node = workflow.get_node(edge.target_id)
                if next_node:
                    await self._execute_node(workflow, next_node, context)
        elif node.type == NodeType.PARALLEL:
            # Execute all branches in parallel
            await self._execute_parallel(workflow, outgoing_edges, context)
        else:
            # Normal: follow first edge
            if outgoing_edges:
                next_node = workflow.get_node(outgoing_edges[0].target_id)
                if next_node:
                    await self._execute_node(workflow, next_node, context)

    def _pick_condition_edge(
        self,
        condition_result: Any,
        edges: List[WorkflowEdge],
    ) -> Optional[WorkflowEdge]:
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
        edges: List[WorkflowEdge],
        context: ExecutionContext,
    ) -> None:
        """Execute multiple branches in parallel."""
        tasks = []
        for edge in edges:
            next_node = workflow.get_node(edge.target_id)
            if next_node:
                task = self._execute_node(workflow, next_node, context)
                tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks)

    async def _invoke_handler(
        self, handler: NodeHandler, node: WorkflowNode, context: ExecutionContext
    ) -> Any:
        """Invoke handler handling both sync and async."""
        result = handler(node, context)
        if asyncio.iscoroutine(result):
            return await result
        return result

    # Default handlers

    def _handle_start(self, node: WorkflowNode, context: ExecutionContext) -> Any:
        """Handle start node."""
        return context.get_variable("input")

    def _handle_end(self, node: WorkflowNode, context: ExecutionContext) -> Any:
        """Handle end node."""
        return context.get_last_output()

    def _handle_transform(self, node: WorkflowNode, context: ExecutionContext) -> Any:
        """Handle transform node."""
        # Apply transformation from config
        transform_fn = node.config.get("transform")
        input_data = context.get_last_output()

        if callable(transform_fn):
            return transform_fn(input_data)

        # Default: pass through
        return input_data

    def _handle_condition(self, node: WorkflowNode, context: ExecutionContext) -> bool:
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
