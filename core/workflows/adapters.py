"""Adapters composing existing multi-agent primitives into workflow graphs.

The AGENT node contract is ``async run(prompt)`` returning an object with
``.output`` (see :func:`core.workflows.node_handlers.handle_agent`). These
adapters bridge primitives with different signatures onto that contract, so
they compose into graphs — and inherit durable execution — without new
executor machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class _AdapterResult:
    """Minimal AGENT-node result shape (``.output`` is what the node emits)."""

    output: Any


class CrewNodeAdapter:
    """Run a :class:`~core.agent.crew.Crew` as an AGENT workflow node.

    ``Crew.run`` takes an ``inputs`` mapping and returns a ``CrewResult``;
    the adapter feeds the node's prompt in under ``input_key`` and exposes
    ``CrewResult.final`` as the node output::

        executor = WorkflowExecutor(agents={"crew": CrewNodeAdapter(crew)})
        WorkflowBuilder("g").start().agent("analysis", agent_id="crew").end()
    """

    def __init__(self, crew: Any, input_key: str = "input") -> None:
        """
        Args:
            crew: The crew (anything exposing ``async run(inputs) -> .final``).
            input_key: Placeholder name the node prompt is bound to — task
                descriptions reference it as ``{<input_key>}``.
        """
        self._crew = crew
        self._input_key = input_key

    async def run(self, prompt: str) -> _AdapterResult:
        """Execute the crew with ``prompt`` bound to the configured input key."""
        result = await self._crew.run(inputs={self._input_key: prompt})
        return _AdapterResult(output=getattr(result, "final", result))


class ColonyNodeAdapter:
    """Run a :class:`~core.swarm.colony.Colony` task as an AGENT workflow node.

    The node prompt becomes a swarm :class:`~core.swarm.types.Task`
    (description = prompt), allocated by the colony's auction and executed via
    the caller-supplied ``execute_fn`` (the same callback contract as
    ``Colony.execute_batch``). The single task's output becomes the node
    output; an allocation failure or execution failure raises so the graph's
    retry/error semantics apply.
    """

    def __init__(
        self,
        colony: Any,
        execute_fn: Any,
        required_capabilities: list[str] | None = None,
    ) -> None:
        """
        Args:
            colony: The colony (anything exposing ``async execute_batch``).
            execute_fn: Async ``(task, agent) -> result`` callback that
                performs the actual work for the allocated pair.
            required_capabilities: Capability filter for the auction.
        """
        self._colony = colony
        self._execute_fn = execute_fn
        self._required_capabilities = list(required_capabilities or [])

    async def run(self, prompt: str) -> _AdapterResult:
        """Auction and execute one swarm task built from ``prompt``."""
        from core.swarm.types import Task

        task = Task(
            description=prompt,
            required_capabilities=list(self._required_capabilities),
        )
        batch = await self._colony.execute_batch([task], self._execute_fn)
        if task.id in batch.completed:
            return _AdapterResult(output=batch.completed[task.id])
        if task.id in batch.failed:
            raise RuntimeError(f"Colony task failed: {batch.failed[task.id]}")
        raise RuntimeError(
            f"Colony task unassigned: no agent won the auction for {task.id!r}"
        )


__all__ = ["ColonyNodeAdapter", "CrewNodeAdapter"]
