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

**Durable runs.** Given a
:class:`~core.orchestration.checkpoint.CheckpointManager`, each approved call
is recorded through ``run_step`` — the same step wrapper the ReAct loop uses —
so a resumed ``Agent.run`` replays the recorded observation instead of calling
the tool again, in whatever order the regenerated turn asks for it. Those turns
run their calls one at a time, as the ReAct loop does under a checkpoint:
per-step saves must not interleave.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from core.agent._tool_dispatch import prepare_tool_call, run_prepared_call
from core.orchestration.call_keys import CallOccurrences
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
    occurrences: CallOccurrences | None = None,
    checkpoint: Any | None = None,
) -> list[tuple[str, bool]]:
    """Run one turn's calls and return ``(observation, is_error)`` in order.

    Each approved call draws its occurrence — how many identical calls the run
    already requested — before anything executes, from the checkpoint when
    there is one (so its step key and the ledger key inside it agree) and from
    ``occurrences`` otherwise. That, not the call's position, is what the
    ledger and the checkpoint key it by.

    Args:
        agent: The owning agent.
        calls: The calls the model emitted this turn.
        context: The gate context from ``gate_context``.
        run_id: Identifier shared by every attempt at this run.
        step_offset: Number of calls already made in this run; names only the
            legacy positional ledger key.
        occurrences: The run's occurrence counter; a fresh one when omitted.
        checkpoint: Optional
            :class:`~core.orchestration.checkpoint.CheckpointManager` that
            records each call as a replayable step.

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

    if runnable and checkpoint is not None:
        tenant_id = _checkpoint_tenant(checkpoint)
        for position, definition, call in runnable:
            outcomes[position] = await _run_checkpointed(
                agent,
                checkpoint,
                definition,
                call,
                run_id=run_id,
                step=step_offset + position,
                tenant_id=tenant_id,
            )
    elif runnable:
        counter = occurrences if occurrences is not None else CallOccurrences()
        gate = asyncio.Semaphore(MAX_PARALLEL_TOOL_CALLS)

        async def _one(
            definition: ToolDefinition, call: ToolCall, step: int, occurrence: int
        ) -> tuple[str, bool]:
            async with gate:
                return await run_prepared_call(
                    agent,
                    definition,
                    call,
                    run_id=run_id,
                    step=step,
                    occurrence=occurrence,
                )

        # Occurrences are drawn here, in emission order, before any call runs.
        results = await asyncio.gather(
            *(
                _one(
                    definition,
                    call,
                    step_offset + position,
                    counter.next(call.name, call.arguments),
                )
                for position, definition, call in runnable
            )
        )
        for (position, _, _), outcome in zip(runnable, results, strict=True):
            outcomes[position] = outcome

    return [outcome if outcome is not None else ("", True) for outcome in outcomes]


def _checkpoint_tenant(checkpoint: Any) -> str | None:
    """The tenant recorded on the checkpoint, for the ledger key.

    Preferred over the ambient tenant because a crash-recovery sweep may resume
    the run with none bound, and a key that moved with the ambient context
    would miss every row the original pass wrote.
    """
    recorded = getattr(getattr(checkpoint, "checkpoint", None), "tenant_id", None)
    return recorded if isinstance(recorded, str) and recorded else None


async def _run_checkpointed(
    agent: Agent[Any],
    checkpoint: Any,
    definition: ToolDefinition,
    call: ToolCall,
    *,
    run_id: str | None,
    step: int,
    tenant_id: str | None,
) -> tuple[str, bool]:
    """Run one approved call as a checkpoint step, or replay its record.

    The step stores ``{"observation", "is_error"}`` — JSON, so the Postgres
    store round-trips it — and the ledger claim runs inside the step under the
    same occurrence, so the two layers agree on which call this is.
    """
    arguments = dict(call.arguments or {})
    occurrence = checkpoint.next_occurrence(call.name, arguments)

    async def _execute() -> dict[str, Any]:
        observation, is_error = await run_prepared_call(
            agent,
            definition,
            call,
            run_id=run_id,
            step=step,
            occurrence=occurrence,
            tenant_id=tenant_id,
        )
        return {"observation": observation, "is_error": is_error}

    recorded = await checkpoint.run_step(
        call.name,
        arguments,
        _execute,
        category=definition.category,
        occurrence=occurrence,
    )
    if isinstance(recorded, dict):
        return str(recorded.get("observation", "")), bool(recorded.get("is_error"))
    return str(recorded), False
