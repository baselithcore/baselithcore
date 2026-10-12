"""Batch allocation and execution for :class:`~baselith.core.swarm.colony.Colony`.

Split from :mod:`baselith.core.swarm.colony` (module size cap):
:meth:`Colony.execute_batch` auctions every task, then runs the winners
concurrently under one ``TaskGroup``.

**Durable batches.** When a checkpoint is ambient — a workflow AGENT node
running a :class:`~baselith.core.workflows.adapters.ColonyNodeAdapter`, or any
caller that binds one (see :mod:`baselith.core.orchestration.checkpoint_scope`)
— the batch gets a child scope of it and records, per task:

* the **auction**: every bid and the winner (or that nobody won), so a resumed
  batch reinstates the same assignment instead of re-running an auction whose
  inputs — agent load, success rates, pheromones — live in memory and were lost
  with the crash;
* the **execution**: the result ``execute_fn`` returned for the winner, so a
  finished task is never executed again. ``execute_fn`` runs with the task's own
  child scope bound as the ambient checkpoint, so the durable work inside it (an
  agent's turns and tool calls, a crew) records under that task and a task
  interrupted half-way resumes from its own steps.

A task that failed is not recorded as a result: the exception leaves the step
unrecorded and the task runs again on resume, as a failed swarm subtask does.
Results must be JSON-serialisable to be persisted (the same rule as any
workflow node output). Tasks are identified by their content (description,
capabilities, priority, parameters) and position in the batch — never by
``Task.id``, which is random per construction.

A recorded winner that is no longer registered in the resumed colony can still
replay a recorded result; if its execution was never recorded, the task fails
with a message naming the agent rather than silently re-auctioning.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.checkpoint_scope import (
    bind_checkpoint,
    current_checkpoint,
)

if TYPE_CHECKING:
    from baselith.core.swarm.colony import Colony
    from baselith.core.swarm.types import AgentProfile, Task

logger = get_logger(__name__)

__all__ = ["run_batch", "task_identity"]


def task_identity(task: Task) -> dict[str, Any]:
    """The content of ``task`` that names it across passes (no random id)."""
    return {
        "description": task.description,
        "capabilities": list(task.required_capabilities),
        "priority": task.priority.name,
        "parameters": task.parameters,
    }


async def _auction(
    colony: Colony, task: Task, scope: Any | None, index: int
) -> str | None:
    """Auction ``task``; under a scope, record or replay the outcome."""
    if scope is None:
        return await colony.submit_task(task)

    async def _live() -> dict[str, Any]:
        winner = await colony.submit_task(task)
        bids = [
            {
                "agent_id": bid.agent_id,
                "score": bid.score,
                "estimated_time": bid.estimated_time,
                "confidence": bid.confidence,
            }
            for bid in colony.auction.last_bids(task.id)
        ]
        return {"winner": winner, "bids": bids}

    recorded: dict[str, Any] = await scope.run_step(
        "auction",
        {"index": index, "task": task_identity(task)},
        _live,
        category="swarm_auction",
    )
    winner = recorded.get("winner")
    if winner is not None and task.assigned_to != winner:
        # Replayed: reinstate the recorded assignment in this colony.
        colony.restore_assignment(task, str(winner))
    return str(winner) if winner is not None else None


async def _execute(
    colony: Colony,
    task: Task,
    agent: AgentProfile | None,
    winner: str,
    execute_fn: Any,
    scope: Any | None,
    index: int,
) -> tuple[str, bool, Any]:
    """Run (or replay) one allocated task; ``(task_id, ok, payload)``."""
    from baselith.core.orchestration.limits import BudgetExceededError

    async def _live() -> Any:
        if agent is None:
            raise RuntimeError(
                f"auction winner {winner!r} is no longer registered in this colony"
            )
        task_scope = (
            scope.scope("colony_task", {"index": index, "agent": winner})
            if scope is not None
            else None
        )
        with bind_checkpoint(task_scope):
            return await execute_fn(task, agent)

    try:
        if scope is None:
            out = await _live()
        else:
            recorded = await scope.run_step(
                "execute",
                {"index": index, "task": task_identity(task), "agent": winner},
                lambda: _wrap(_live),
                category="swarm_task",
            )
            out = recorded["result"]
        colony.complete_task(task.id, success=True, result=out)
        return task.id, True, out
    except (asyncio.CancelledError, BudgetExceededError):
        colony.complete_task(task.id, success=False)
        raise  # fatal → propagate to cancel the whole group
    except Exception as exc:
        colony.complete_task(task.id, success=False)
        return task.id, False, str(exc)


async def _wrap(live: Any) -> dict[str, Any]:
    """A step value that keeps a ``None`` result distinguishable."""
    return {"result": await live()}


async def run_batch(
    colony: Colony, tasks: list[Task], execute_fn: Any
) -> Colony.BatchResult:
    """Body of :meth:`Colony.execute_batch` (budget already bound)."""
    from baselith.core.orchestration.limits import BudgetExceededError
    from baselith.core.swarm.colony import Colony

    result = Colony.BatchResult()
    parent = current_checkpoint()
    scope = (
        parent.scope("colony_batch", [task_identity(t) for t in tasks])
        if parent is not None
        else None
    )

    # 1. Allocate all tasks via auction (recorded under a checkpoint).
    allocations: list[tuple[int, Task, str]] = []
    for index, task in enumerate(tasks):
        winner_id = await _auction(colony, task, scope, index)
        if winner_id and (winner_id in colony._agents or scope is not None):
            task.assigned_to = winner_id
            allocations.append((index, task, winner_id))
        else:
            result.unassigned.append(task.id)

    if not allocations:
        return result

    # 2. Execute all allocated tasks concurrently under a TaskGroup so a fatal
    #    error deterministically cancels the siblings (structured
    #    concurrency), rather than leaving orphaned tasks running as
    #    ``gather`` could. A per-request LoopBudget breach is fatal to the
    #    whole batch: the sub-agents share the ambient budget, so once it is
    #    exhausted no sibling can make progress.
    running: list[asyncio.Task[tuple[str, bool, Any]]] = []
    budget_error: BudgetExceededError | None = None
    try:
        async with asyncio.TaskGroup() as tg:
            running = [
                tg.create_task(
                    _execute(
                        colony,
                        task,
                        colony._agents.get(winner),
                        winner,
                        execute_fn,
                        scope,
                        index,
                    )
                )
                for index, task, winner in allocations
            ]
    except* BudgetExceededError as eg:
        # Siblings were cancelled by the group; re-raise after harvesting the
        # tasks that already finished.
        budget_error = eg.exceptions[0]  # type: ignore[assignment]

    for tt, (_, task, _winner) in zip(running, allocations, strict=True):
        if tt.cancelled():
            result.failed.setdefault(task.id, "cancelled: batch budget exceeded")
            continue
        if tt.exception() is not None:
            continue  # the fatal error is handled via budget_error
        task_id, ok, payload = tt.result()
        if ok:
            result.completed[task_id] = payload
        else:
            result.failed[task_id] = payload

    if budget_error is not None:
        raise budget_error
    return result
