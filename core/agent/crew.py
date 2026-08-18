"""Declarative multi-agent crews over the typed :class:`~core.agent.Agent`.

The ten-line collaborative counterpart to the single-agent hello world:

    from core.agent import Agent, Crew, Task

    researcher = Agent(system_prompt="You are a meticulous researcher.")
    writer = Agent(system_prompt="You write crisp executive summaries.")

    crew = Crew(
        agents=[researcher, writer],
        tasks=[
            Task("Research {topic} and list the key facts.", agent=researcher),
            Task("Write a summary from the research.", agent=writer),
        ],
    )
    result = await crew.run(inputs={"topic": "vector databases"})
    result.final  # -> the last task's output

Every task executes through :meth:`Agent.run`, so tools, ``output_type``
validation, cost accounting, and the ambient ``LoopBudget`` all apply
unchanged. ``process="sequential"`` (default) threads each task's output into
the next task's prompt as context; ``process="parallel"`` runs independent
tasks concurrently. For richer coordination (auctions, handoffs, pheromones)
drop down to :mod:`core.swarm`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from core.agent.agent import Agent
from core.observability.logging import get_logger

logger = get_logger(__name__)

_PROCESSES = ("sequential", "parallel")


class _SafeFormatDict(dict):
    """Leave unknown ``{placeholders}`` intact instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@dataclass
class Task:
    """One unit of crew work.

    Attributes:
        description: The task prompt. ``{placeholders}`` are filled from
            ``Crew.run(inputs=...)``; unknown placeholders are left intact.
        agent: The agent responsible. Optional only when the crew has exactly
            one agent (auto-assigned).
        name: Optional label used in :class:`TaskResult` (defaults to
            ``task-<index>``).
        expected_output: Optional description of the expected deliverable,
            appended to the prompt.
    """

    description: str
    agent: Agent | None = None
    name: str | None = None
    expected_output: str | None = None


@dataclass(frozen=True)
class TaskResult:
    """Outcome of one crew task.

    Attributes:
        name: Task label.
        output: The task agent's validated output (``output_type`` instance
            when the agent declares one, plain text otherwise).
        text: The raw final text of the task.
        agent_index: Index of the executing agent in ``Crew.agents``.
    """

    name: str
    output: Any
    text: str
    agent_index: int


@dataclass(frozen=True)
class CrewResult:
    """Outcome of one :meth:`Crew.run` call, in task order."""

    task_results: list[TaskResult] = field(default_factory=list)

    @property
    def final(self) -> Any:
        """The last task's output — the crew's deliverable."""
        return self.task_results[-1].output if self.task_results else None


class Crew:
    """A set of agents executing a task list.

    Args:
        agents: The crew members. A task without an explicit ``agent`` is
            auto-assigned only when there is exactly one member.
        tasks: The work, executed in order (``sequential``) or concurrently
            (``parallel``).
        process: ``"sequential"`` (default — each task's prompt receives the
            accumulated outputs of prior tasks as context) or ``"parallel"``
            (independent tasks, no cross-task context).

    Raises:
        ValueError: Empty task list, unknown ``process``, or a task without
            an agent in a multi-agent crew.
    """

    def __init__(
        self,
        agents: Sequence[Agent],
        tasks: Sequence[Task],
        *,
        process: str = "sequential",
    ) -> None:
        if not tasks:
            raise ValueError("Crew needs at least one task.")
        if process not in _PROCESSES:
            raise ValueError(
                f"Unknown process {process!r}; expected one of {_PROCESSES}."
            )
        self.agents = list(agents)
        self.tasks = list(tasks)
        self.process = process
        for index, task in enumerate(self.tasks):
            if task.agent is None:
                if len(self.agents) == 1:
                    task.agent = self.agents[0]
                else:
                    raise ValueError(
                        f"Task {task.name or index} has no agent assigned; "
                        "explicit assignment is required when the crew has "
                        "more than one agent."
                    )

    # -- internals ---------------------------------------------------------

    def _task_prompt(
        self, task: Task, inputs: dict[str, Any], context: list[tuple[str, str]]
    ) -> str:
        prompt = task.description.format_map(_SafeFormatDict(inputs))
        if task.expected_output:
            prompt += f"\n\nExpected output: {task.expected_output}"
        if context:
            rendered = "\n\n".join(
                f"[{name}]\n{text}" for name, text in context if text
            )
            prompt += f"\n\nContext from previous tasks:\n{rendered}"
        return prompt

    def _agent_index(self, agent: Agent) -> int:
        for index, member in enumerate(self.agents):
            if member is agent:
                return index
        return -1  # assigned agent outside the roster — allowed, flagged as -1

    async def _run_task(
        self,
        index: int,
        task: Task,
        inputs: dict[str, Any],
        context: list[tuple[str, str]],
    ) -> TaskResult:
        assert task.agent is not None  # guaranteed by __init__
        name = task.name or f"task-{index}"
        prompt = self._task_prompt(task, inputs, context)
        logger.debug("crew_task_start name=%s", name)
        result = await task.agent.run(prompt)
        return TaskResult(
            name=name,
            output=result.output,
            text=result.text,
            agent_index=self._agent_index(task.agent),
        )

    # -- public API --------------------------------------------------------

    async def run(self, inputs: dict[str, Any] | None = None) -> CrewResult:
        """Execute the task list and return per-task results in order."""
        inputs = inputs or {}
        if self.process == "parallel":
            results = await asyncio.gather(
                *(
                    self._run_task(i, task, inputs, [])
                    for i, task in enumerate(self.tasks)
                )
            )
            return CrewResult(task_results=list(results))

        context: list[tuple[str, str]] = []
        task_results: list[TaskResult] = []
        for index, task in enumerate(self.tasks):
            task_result = await self._run_task(index, task, inputs, context)
            task_results.append(task_result)
            context.append((task_result.name, task_result.text))
        return CrewResult(task_results=task_results)


__all__ = ["Crew", "CrewResult", "Task", "TaskResult"]
