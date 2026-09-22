"""How a typed agent's tool calls actually run: off the loop, bounded, overlapped.

:mod:`core.agent._tool_dispatch` owns everything *around* a call — argument
validation, the enforcement chokepoint, rendering, the idempotency ledger.
This module owns the execution itself, and the three properties the ReAct
executor already had and the typed loop did not:

* **Off the event loop.** A synchronous tool ran inline, so one blocking HTTP
  client or file read stalled every other in-flight request in the process for
  its whole duration. ``tools=[fn]`` invites exactly such callables.
* **Bounded.** There was no per-call deadline anywhere in the typed loop, so a
  tool that never returned pinned the agent forever. The cap is shrunk to the
  ambient :class:`~core.orchestration.limits.LoopBudget`'s remaining wall clock
  so a tool cannot outlive the request whose result it is computing.
* **Overlapped.** A provider emits every call of a multi-tool turn before
  seeing any result, so those calls are independent by construction. Running
  them one at a time paid the sum of their latencies instead of the slowest.

The gates stay strictly sequential ahead of execution: approval and budget
refusals are fail-closed and abort the turn, so a tool later in the turn must
not already have run when an earlier one is denied.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from core.agent._tool_dispatch import prepare_tool_call, run_prepared_call
from core.reasoning.react_tools import MAX_PARALLEL_TOOL_CALLS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.agent.agent import Agent
    from core.reasoning.react import ToolDefinition
    from core.services.llm.tool_calling import ToolCall

__all__ = ["effective_tool_timeout", "execute_tool_calls", "invoke_tool"]


def effective_tool_timeout(agent: Agent[Any]) -> float | None:
    """Per-call deadline: the agent's cap, shrunk to what the budget has left.

    Args:
        agent: The agent whose ``tool_timeout`` applies.

    Returns:
        Seconds, or ``None`` for no deadline at all (no cap configured and no
        ambient budget).
    """
    try:
        from core.orchestration.budget_context import get_active_budget

        budget = get_active_budget()
        remaining = budget.remaining_seconds() if budget is not None else None
    except Exception:  # silent-ok: no readable budget = the static cap, never longer
        remaining = None

    cap = agent.tool_timeout
    if remaining is None:
        return cap
    if cap is None:
        return max(remaining, 0.001)
    return max(min(cap, remaining), 0.001)


async def invoke_tool(
    agent: Agent[Any], definition: ToolDefinition, call: ToolCall
) -> Any:
    """Call one tool and return its raw value.

    Cancelling on timeout stops the *await*, not a thread already running a
    synchronous tool — Python cannot interrupt one. The agent stops waiting
    and reports the timeout; a runaway thread is the tool's own to bound.

    Rendering the value for the model (JSON encoding, ``SkillResult``
    unpacking, truncation, the injection scan, the untrusted envelope) happens
    at the single seam in :mod:`core.agent._tool_dispatch`; doing any of it
    here would make this a second one.

    Args:
        agent: The owning agent, for its deadline.
        definition: The tool to run.
        call: What the model asked for.

    Returns:
        Whatever the tool returned.

    Raises:
        TimeoutError: The deadline elapsed.
    """
    arguments = dict(call.arguments or {})
    fn = definition.fn

    async def _run() -> Any:
        if inspect.iscoroutinefunction(fn):
            return await fn(**arguments)
        result = await asyncio.to_thread(fn, **arguments)
        # A sync callable can still return an awaitable (a partial wrapping a
        # coroutine function, say). Constructing a coroutine binds no loop, so
        # awaiting it here is correct even though the thread built it.
        return await result if inspect.isawaitable(result) else result

    # ``asyncio.timeout(None)`` is a no-op deadline: one path, not two.
    async with asyncio.timeout(effective_tool_timeout(agent)):
        return await _run()


async def execute_tool_calls(
    agent: Agent[Any],
    calls: list[ToolCall],
    *,
    context: dict[str, Any],
    run_id: str | None,
    step_offset: int,
) -> list[tuple[str, bool]]:
    """Run one turn's calls and return ``(observation, is_error)`` in order.

    Ledger keys stay identical to the sequential numbering they replaced: each
    call's step is its position in the run, assigned before anything executes
    rather than as results arrive, so a resumed run matches the same keys.

    Args:
        agent: The owning agent.
        calls: The calls the model emitted this turn.
        context: The gate context from ``gate_context``.
        run_id: Identifier shared by every attempt at this run.
        step_offset: Number of calls already made in this run.

    Returns:
        One ``(observation, is_error)`` pair per call, in the order asked.

    Raises:
        ApprovalPendingError: A durable human-in-the-loop pause.
        BudgetExceededError: A per-request cap was hit (fail-closed).
    """
    if not calls:
        return []

    outcomes: list[tuple[str, bool] | None] = [None] * len(calls)
    runnable: list[tuple[int, ToolDefinition, ToolCall]] = []

    # Gate phase: strictly in order, nothing running yet.
    for position, call in enumerate(calls):
        definition, early = await prepare_tool_call(agent, call, context)
        if definition is None:
            assert early is not None
            outcomes[position] = early
            continue
        runnable.append((position, definition, call))

    if runnable:
        gate = asyncio.Semaphore(MAX_PARALLEL_TOOL_CALLS)

        async def _one(
            definition: ToolDefinition, call: ToolCall, step: int
        ) -> tuple[str, bool]:
            async with gate:
                return await run_prepared_call(
                    agent, definition, call, run_id=run_id, step=step
                )

        results = await asyncio.gather(
            *(
                _one(definition, call, step_offset + position)
                for position, definition, call in runnable
            )
        )
        for (position, _, _), outcome in zip(runnable, results, strict=True):
            outcomes[position] = outcome

    return [outcome if outcome is not None else ("", True) for outcome in outcomes]
