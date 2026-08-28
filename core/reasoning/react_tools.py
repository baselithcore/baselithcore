"""Guarded tool execution for the ReAct loop.

Holds everything between "the model asked for a tool" and "here is the
observation": the contract / autonomy / budget gates, the timeout and retry
policy, and the consecutive-failure circuit breaker.

Mixed into :class:`core.reasoning.react.ReActAgent`; not useful standalone.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from core.observability.logging import get_logger
from core.orchestration.tool_output import truncate_tool_output
from core.reasoning.react_types import ToolDefinition

logger = get_logger(__name__)

#: Ceiling on tool calls executed concurrently within one multi-tool turn.
#: Models emit a handful per turn; the bound is there so a pathological fan-out
#: cannot open an unbounded number of sockets or thread-pool slots at once.
MAX_PARALLEL_TOOL_CALLS = 8


class ToolExecutionMixin:
    """Tool dispatch, gating and failure accounting for :class:`ReActAgent`.

    Expects the host class to provide ``_tools``, ``_tool_timeout``,
    ``_tool_retries``, ``_retry_backoff``, ``_autonomy_policy``,
    ``_human_intervention``, ``_contract_validator``, ``_loop_budget``,
    ``_checkpoint``, ``_max_consecutive_tool_failures``, ``_failure_streak``
    and ``_stall_guard``.
    """

    _tools: dict[str, ToolDefinition]
    _tool_timeout: float | None
    _tool_retries: int
    _retry_backoff: float
    _autonomy_policy: Any | None
    _human_intervention: Any | None
    _contract_validator: Any | None
    _loop_budget: Any | None
    _checkpoint: Any | None
    _max_consecutive_tool_failures: int | None
    _failure_streak: int
    _stall_guard: Any | None

    async def _execute_tool(self, name: str, args_raw: str) -> str:
        """Execute a text-parsed tool call (positional args from raw string)."""
        args = [a.strip().strip("\"'") for a in args_raw.split(",") if a.strip()]
        return await self._run_tool_guarded(name, tuple(args), {})

    async def _execute_tool_call(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a structured (native) tool call with keyword arguments."""
        return await self._run_tool_guarded(name, (), dict(arguments))

    async def _execute_tool_calls(
        self, calls: list[tuple[str, dict[str, Any]]]
    ) -> list[str]:
        """Run one turn's ``(name, arguments)`` calls, observations in order.

        A native multi-tool turn emits every call before seeing any result, so
        the calls are independent by construction and running them one at a
        time paid the sum of their latencies instead of the slowest.

        The **gates** still run sequentially, ahead of any execution: approval
        and budget refusals are fail-closed and abort the turn, so a tool later
        in the turn must not already have run when an earlier one is denied.
        Only the approved invocations overlap, bounded so a wide fan-out cannot
        swamp the tool backends.
        """
        if not calls:
            return []

        observations: list[str | None] = [None] * len(calls)
        runnable: list[tuple[int, ToolDefinition, dict[str, Any]]] = []

        for index, (name, arguments) in enumerate(calls):
            tool = self._tools.get(name)
            if tool is None:
                observations[index] = (
                    f"Error: unknown tool '{name}'. Available tools: {list(self._tools)}"
                )
                continue
            # Propagates ApprovalPendingError / BudgetExceededError, which must
            # abort the turn before any later tool in it executes.
            denial = await self._enforce_tool_gates(tool)
            if denial is not None:
                observations[index] = denial
                continue
            runnable.append((index, tool, dict(arguments)))

        if runnable:
            gate = asyncio.Semaphore(MAX_PARALLEL_TOOL_CALLS)

            async def _run(tool: ToolDefinition, kwargs: dict[str, Any]) -> str:
                async with gate:
                    return await self._invoke_tool(tool, (), kwargs)

            results = await asyncio.gather(
                *(_run(tool, kwargs) for _, tool, kwargs in runnable)
            )
            for (index, _, _), observation in zip(runnable, results, strict=True):
                observations[index] = observation

        return [o if o is not None else "" for o in observations]

    def _effective_tool_timeout(self) -> float | None:
        """Per-call timeout: the configured cap, shrunk to the ambient
        LoopBudget's remaining wall-clock so one tool can't outlive the
        request deadline. Falls back to the static cap outside an
        orchestrated request."""
        try:
            from core.orchestration.budget_context import get_active_budget

            budget = get_active_budget()
            remaining = budget.remaining_seconds() if budget is not None else None
        except Exception:
            remaining = None
        if remaining is None:
            return self._tool_timeout
        if self._tool_timeout is None:
            return max(remaining, 0.001)
        return max(min(self._tool_timeout, remaining), 0.001)

    def _active_budget(self) -> Any | None:
        """Explicit LoopBudget when injected, else the ambient request budget."""
        if self._loop_budget is not None:
            return self._loop_budget
        try:
            from core.orchestration.budget_context import get_active_budget

            return get_active_budget()
        except Exception:
            return None

    async def _enforce_tool_gates(self, tool: ToolDefinition) -> str | None:
        """Apply contract / autonomy / budget gates before a tool runs.

        Returns an error-observation string when the call is denied (the loop
        continues and the model can adapt), or None when the call may proceed.
        Fail-closed exceptions propagate: ``ApprovalPendingError`` (durable
        HITL pause) and ``BudgetExceededError`` (tool-call cap) abort the run.
        """
        from core.orchestration.autonomy import (
            ApprovalPendingError,
            ApprovalRequiredError,
            enforce_approval,
        )
        from core.orchestration.contract import ContractViolationError

        if self._contract_validator is not None:
            try:
                self._contract_validator.check_tool_call(tool.name)
            except ContractViolationError as exc:
                logger.warning(
                    "ReAct tool '%s' blocked by contract: %s", tool.name, exc
                )
                return f"Error executing '{tool.name}': {exc}"

        if self._autonomy_policy is not None:
            try:
                await enforce_approval(
                    self._autonomy_policy,
                    tool.category,
                    tool.name,
                    self._human_intervention,
                    checkpoint=self._checkpoint,
                )
            except ApprovalPendingError:
                # Durable pause — the checkpoint is already awaiting_approval.
                raise
            except ApprovalRequiredError as exc:
                logger.warning(
                    "ReAct tool '%s' blocked by autonomy policy: %s", tool.name, exc
                )
                return f"Error executing '{tool.name}': {exc}"

        budget = self._active_budget()
        if budget is not None:
            # Raises BudgetExceededError at the cap: fail-closed, a runaway
            # loop cannot keep dispatching tools.
            budget.record_tool_call()
        return None

    def _note_tool_outcome(self, observation: str) -> str | None:
        """Track the consecutive-failure streak; return an escalation message
        when the configured cap is crossed, else None.

        Failed observations are the error strings produced by the guarded
        executor (``Error ...``); any success resets the streak. Escalating
        early keeps a broken tool from burning the whole iteration budget.
        """
        cap = self._max_consecutive_tool_failures
        if cap is None and self._stall_guard is None:
            return None
        if observation.startswith("Error"):
            self._failure_streak += 1
        else:
            self._failure_streak = 0
            return None

        # Futility check: the streak counts *how many* failures; the stall
        # guard counts how many times the *same* failure came back. A tool
        # that keeps returning a different error each time is still making
        # the loop pay for nothing.
        if self._stall_guard is not None:
            verdict = self._stall_guard.record(observation)
            if verdict.stalled:
                logger.warning(
                    "ReAct: %s — escalating instead of continuing the loop.",
                    verdict.reason,
                )
                return (
                    f"Stopping: {verdict.reason} (last: {observation}). "
                    "Please review the tool configuration or retry later."
                )

        if cap is None or self._failure_streak < cap:
            return None
        logger.warning(
            "ReAct: %d consecutive tool failures — escalating instead of "
            "continuing the loop.",
            self._failure_streak,
        )
        return (
            f"Stopping: tools failed {self._failure_streak} consecutive times "
            f"(last: {observation}). Please review the tool configuration or "
            "retry later."
        )

    async def _run_tool_guarded(
        self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'. Available tools: {list(self._tools)}"

        denial = await self._enforce_tool_gates(tool)
        if denial is not None:
            return denial

        return await self._invoke_tool(tool, args, kwargs)

    async def _invoke_tool(
        self, tool: ToolDefinition, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        """Run an already-gated tool, applying timeout, retries and truncation.

        Split from :meth:`_run_tool_guarded` so a multi-tool turn can gate its
        calls in order and then overlap only the invocations.
        """
        name = tool.name

        async def _invoke() -> Any:
            if inspect.iscoroutinefunction(tool.fn):
                coro = tool.fn(*args, **kwargs)
            else:
                coro = asyncio.to_thread(tool.fn, *args, **kwargs)
            timeout = self._effective_tool_timeout()
            if timeout is not None:
                return await asyncio.wait_for(coro, timeout=timeout)
            return await coro

        for attempt in range(self._tool_retries + 1):
            try:
                result = await _invoke()
                # Cap the observation so a large tool result can't
                # bloat/overflow the context window on the next reasoning turn.
                return truncate_tool_output(str(result))
            except TimeoutError:
                # Also reachable via a tool's own socket timeout (builtin
                # TimeoutError subclasses OSError, so this clause must come
                # first) — hence the None-safe wording.
                after = (
                    f" after {self._tool_timeout:.1f}s"
                    if self._tool_timeout is not None
                    else ""
                )
                logger.warning("Tool '%s' timed out%s", name, after)
                return f"Error executing '{name}': timed out{after}"
            except (ConnectionError, OSError) as exc:
                if attempt < self._tool_retries:
                    delay = self._retry_backoff * (2**attempt)
                    logger.warning(
                        "Tool '%s' transient failure (%s), retry %d/%d in %.1fs",
                        name,
                        type(exc).__name__,
                        attempt + 1,
                        self._tool_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning("Tool '%s' raised %s: %s", name, type(exc).__name__, exc)
                return f"Error executing '{name}': {exc}"
            except Exception as exc:
                logger.warning("Tool '%s' raised %s: %s", name, type(exc).__name__, exc)
                return f"Error executing '{name}': {exc}"
        # Unreachable: every path in the loop returns.
        return f"Error executing '{name}': exhausted retries"


__all__ = ["ToolExecutionMixin"]
