"""
Parallel Tool Execution.

Implements the LLMCompiler pattern for executing independent tool calls
concurrently to reduce end-to-end latency.
"""

import asyncio
from core.observability.logging import get_logger
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import uuid4

logger = get_logger(__name__)


class ToolStatus(Enum):
    """Execution status of a tool call."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ToolCall:
    """Represents a tool invocation request."""

    id: str = field(default_factory=lambda: str(uuid4())[:8])
    tool_name: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    """IDs of tool calls that must complete before this one."""

    status: ToolStatus = ToolStatus.PENDING
    timeout_seconds: float = 30.0


@dataclass
class ToolResult:
    """Result of a tool execution."""

    call_id: str
    tool_name: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0


@dataclass
class ExecutionPlan:
    """Execution plan with parallelization analysis."""

    calls: List[ToolCall]
    parallel_groups: List[List[str]]
    """Groups of tool call IDs that can run in parallel."""

    total_estimated_latency_ms: float = 0.0
    parallelization_factor: float = 1.0


class ParallelToolExecutor:
    """
    Execute independent tool calls in parallel.

    Implements the LLMCompiler pattern:
    1. Analyze tool call dependencies
    2. Group independent calls for parallel execution
    3. Execute groups sequentially, calls within groups in parallel

    Example:
        >>> executor = ParallelToolExecutor()
        >>> executor.register_tool("search", search_fn)
        >>> executor.register_tool("fetch", fetch_fn)
        >>> calls = [ToolCall(tool_name="search", ...), ToolCall(tool_name="fetch", ...)]
        >>> results = await executor.execute_parallel(calls)
    """

    def __init__(
        self,
        max_parallel: int = 5,
        default_timeout: float = 30.0,
    ):
        """
        Initialize parallel executor.

        Args:
            max_parallel: Maximum concurrent tool executions
            default_timeout: Default timeout for tool calls
        """
        self.max_parallel = max_parallel
        self.default_timeout = default_timeout
        self._tools: Dict[str, Callable] = {}
        self._semaphore = asyncio.Semaphore(max_parallel)

    def register_tool(self, name: str, handler: Callable) -> None:
        """
        Register a tool handler.

        Args:
            name: Tool name
            handler: Async callable that executes the tool
        """
        self._tools[name] = handler

    def analyze_dependencies(self, calls: List[ToolCall]) -> ExecutionPlan:
        """
        Deconstruct tool calls into an optimal parallel execution plan.

        Analyzes the dependency graph of tool invocations to identify
        groups of calls that can be safely run concurrently without
        violating ordering constraints.

        Args:
            calls: A collection of tool call requests, potentially with
                   cross-dependencies.

        Returns:
            ExecutionPlan: A structured plan containing sequential groups
                          of parallel tool-call IDs.
        """
        if not calls:
            return ExecutionPlan(calls=[], parallel_groups=[])

        # Build dependency graph
        call_map = {c.id: c for c in calls}
        completed: Set[str] = set()
        groups: List[List[str]] = []

        remaining = set(call_map.keys())

        while remaining:
            # Find calls with satisfied dependencies
            ready = []
            for call_id in remaining:
                call = call_map[call_id]
                deps_satisfied = all(d in completed for d in call.dependencies)
                if deps_satisfied:
                    ready.append(call_id)

            if not ready:
                # Circular dependency or invalid deps - force progress
                ready = [next(iter(remaining))]
                logger.warning(
                    f"Forcing execution of {ready[0]} due to unresolved deps"
                )

            groups.append(ready)
            for call_id in ready:
                completed.add(call_id)
                remaining.discard(call_id)

        # Calculate parallelization factor
        sequential_count = len(calls)
        parallel_count = len(groups)
        factor = sequential_count / parallel_count if parallel_count > 0 else 1.0

        return ExecutionPlan(
            calls=calls,
            parallel_groups=groups,
            parallelization_factor=factor,
        )

    async def execute_parallel(
        self,
        calls: List[ToolCall],
    ) -> List[ToolResult]:
        """
        High-concurrency dispatcher for tool execution.

        Generates an execution plan and iteratively dispatches parallel
        groups to the event loop. Uses an internal semaphore to bound
        global concurrency.

        Args:
            calls: Detailed list of tool calls to satisfy.

        Returns:
            List[ToolResult]: Results mapped back to the original call
                             order, including success/failure telemetry.
        """
        plan = self.analyze_dependencies(calls)
        results_map: Dict[str, ToolResult] = {}

        for group in plan.parallel_groups:
            # Execute group in parallel
            group_calls = [c for c in calls if c.id in group]
            group_results = await asyncio.gather(
                *[self._execute_single(c) for c in group_calls],
                return_exceptions=True,
            )

            # Process results
            for call, result in zip(group_calls, group_results):
                if isinstance(result, BaseException):
                    results_map[call.id] = ToolResult(
                        call_id=call.id,
                        tool_name=call.tool_name,
                        success=False,
                        error=str(result),
                    )
                else:
                    results_map[call.id] = result

        # Return in original order
        return [results_map[c.id] for c in calls if c.id in results_map]

    async def _execute_single(self, call: ToolCall) -> ToolResult:
        """
        Execute a single tool call within a concurrency-guarded scope.

        Manages registration lookup, parameter injection, error handling,
        and execution timing logic.

        Args:
            call: The tool invocation request.

        Returns:
            ToolResult: Enriched metadata about the call's outcome.
        """
        import time

        start = time.perf_counter()
        call.status = ToolStatus.RUNNING

        async with self._semaphore:
            handler = self._tools.get(call.tool_name)
            if not handler:
                call.status = ToolStatus.FAILED
                return ToolResult(
                    call_id=call.id,
                    tool_name=call.tool_name,
                    success=False,
                    error=f"Unknown tool: {call.tool_name}",
                )

            try:
                timeout = call.timeout_seconds or self.default_timeout
                result = await asyncio.wait_for(
                    handler(**call.parameters),
                    timeout=timeout,
                )
                call.status = ToolStatus.COMPLETED

                elapsed_ms = (time.perf_counter() - start) * 1000
                return ToolResult(
                    call_id=call.id,
                    tool_name=call.tool_name,
                    success=True,
                    result=result,
                    execution_time_ms=elapsed_ms,
                )

            except asyncio.TimeoutError:
                call.status = ToolStatus.FAILED
                return ToolResult(
                    call_id=call.id,
                    tool_name=call.tool_name,
                    success=False,
                    error=f"Timeout after {call.timeout_seconds}s",
                )
            except Exception as e:
                call.status = ToolStatus.FAILED
                logger.error(f"Tool {call.tool_name} failed: {e}")
                return ToolResult(
                    call_id=call.id,
                    tool_name=call.tool_name,
                    success=False,
                    error=str(e),
                )

    def get_stats(self) -> Dict[str, Any]:
        """
        Collect performance statistics for the parallel executor.

        Returns:
            Dict[str, Any]: Aggregated metrics for auditing throughput
                           and latency efficiency.
        """
        return {
            "registered_tools": list(self._tools.keys()),
            "max_parallel": self.max_parallel,
            "default_timeout": self.default_timeout,
        }
