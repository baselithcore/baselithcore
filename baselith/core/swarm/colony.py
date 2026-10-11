"""
Swarm Colony Orchestration Module.

Acts as the central nervous system for swarm-based agentic workflows.
Coordinates decentralized task allocation, pheromone-based signaling,
and dynamic team formation to achieve emergent complex behaviors.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from baselith.core.config.swarm import SwarmConfig, get_swarm_config
from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.budget_context import DEFAULT_LIMITS, standalone_budget
from baselith.core.swarm.colony_ops import ColonyOpsMixin, batch_limits

from .auction import TaskAuction
from .pheromones import PheromoneSystem
from .team_formation import TeamFormationEngine
from .types import (
    AgentProfile,
    AgentStatus,
    Handoff,
    HandoffBrief,
    HandoffCycleGuard,
    MessageType,
    Task,
    compress_handoff_context,
)

if TYPE_CHECKING:
    from baselith.core.memory.manager import AgentMemory
    from baselith.core.orchestration.limits import LoopLimits

logger = get_logger(__name__)


class Colony(ColonyOpsMixin):
    """
    Manager for emergent multi-agent coordination.

    Integrates multiple decentralized patterns including task auctions for
    efficient allocation, pheromone systems for state-less signaling,
    and a team engine for dynamic scaling of agent capabilities to meet
    complex goals.
    """

    def __init__(
        self,
        config: SwarmConfig | None = None,
        auction: TaskAuction | None = None,
        pheromones: PheromoneSystem | None = None,
        team_engine: TeamFormationEngine | None = None,
        memory_manager: Optional["AgentMemory"] = None,
        *,
        loop_limits: "LoopLimits | None" = DEFAULT_LIMITS,
    ):
        """
        Initialize swarm colony.

        Args:
            config: Colony configuration
            auction: Optional auction system instance
            pheromones: Optional pheromone system instance
            team_engine: Optional team formation engine instance
            memory_manager: Optional memory orchestration manager
            loop_limits: Caps for an :meth:`execute_batch` with no ambient
                ``LoopBudget``: by default the batch shares one built from the
                orchestrator's defaults; ``None`` disables it.
        """
        self.config = config or get_swarm_config()
        self.memory_manager = memory_manager
        self._loop_limits = loop_limits

        # Core subsystems
        self.auction = auction or TaskAuction(config=self.config.auction)
        self.pheromones = pheromones or PheromoneSystem(
            decay_rate=self.config.pheromone_decay_rate
        )
        self.team_engine = team_engine or TeamFormationEngine(config=self.config.team)

        # Agent registry
        self._agents: dict[str, AgentProfile] = {}

        # Task tracking
        self._tasks: dict[str, Task] = {}
        self._task_results: dict[str, Any] = {}
        self._handoff_guard = HandoffCycleGuard(
            enabled=self.config.handoff_cycle_guard,
            max_hops=self.config.max_handoff_hops,
        )

        # Message handlers
        self._handlers: dict[MessageType, list[Callable]] = {}

    def register_agent(self, agent: AgentProfile) -> None:
        """
        Register an agent with the colony.

        Args:
            agent: Agent profile to register
        """
        self._agents[agent.id] = agent
        self.team_engine.register_agent(agent)
        logger.info(f"Agent registered: {agent.id} ({agent.name})")

    def unregister_agent(self, agent_id: str) -> None:
        """
        Remove an agent from the colony.

        Args:
            agent_id: ID of agent to remove
        """
        if agent_id in self._agents:
            del self._agents[agent_id]
            self.team_engine.unregister_agent(agent_id)
            logger.info(f"Agent unregistered: {agent_id}")

    def get_agent(self, agent_id: str) -> AgentProfile | None:
        """Get agent by ID."""
        return self._agents.get(agent_id)

    def get_available_agents(self) -> list[AgentProfile]:
        """Get all available agents."""
        return [a for a in self._agents.values() if a.is_available]

    async def submit_task(self, task: Task) -> str | None:
        """
        Submit a task for competitive swarm allocation.

        Initializes an auction-based selection process where available agents
        bid based on their capability alignment and current pheromone levels.

        Args:
            task: The structured task defining requirements and objectives.

        Returns:
            Optional[str]: The ID of the winning agent, or None if the
                           task could not be allocated.
        """
        logger.info(f"Task submitted: {task.id} - {task.description}")
        self._tasks[task.id] = task

        # Announce task for auction
        self.auction.announce_task(task)

        # Collect bids from available agents
        for agent in self.get_available_agents():
            if self._should_bid(agent, task):
                bid = self.auction.calculate_bid(agent, task)
                self.auction.submit_bid(bid)

        # Resolve auction
        winner_id: str | None = self.auction.resolve(task.id)

        if winner_id:
            # Track assignment on the task itself (needed by self_heal)
            task.assigned_to = winner_id
            task.status = "assigned"

            # Update agent status
            if winner_id in self._agents:
                self._agents[winner_id].status = AgentStatus.BUSY

        return winner_id

    def _should_bid(self, agent: AgentProfile, task: Task) -> bool:
        """Determine if agent should bid on task."""
        if not agent.is_available:
            return False

        # Check capability match
        score = agent.get_capability_score(task.required_capabilities)
        if score < 0.3:
            return False

        # Check pheromone signals for guidance
        task_type = (
            task.required_capabilities[0] if task.required_capabilities else "general"
        )
        signals = self.pheromones.sense(f"task_type:{task_type}")

        # Avoid if failure pheromones are strong
        if signals.get(PheromoneSystem.FAILURE, 0) > 2.0:
            return False

        return True

    def complete_task(
        self,
        task_id: str,
        success: bool = True,
        result: Any | None = None,
    ) -> None:
        """
        Finalize a task's lifecycle and trigger environmental feedback.

        Updates the task status, records results, frees the assigned agent,
        and deposits success/failure pheromones into the swarm environment.

        Args:
            task_id: Unique identifier for the task.
            success: Boolean flag indicating if the goal was achieved.
            result: Optional payload containing the output of the task.
        """
        task = self._tasks.get(task_id)
        if not task:
            return

        task.status = "completed" if success else "failed"
        self._task_results[task_id] = result

        # Update agent status
        if task.assigned_to and task.assigned_to in self._agents:
            agent = self._agents[task.assigned_to]
            agent.status = AgentStatus.IDLE

            # Update success rate
            if success:
                agent.success_rate = min(1.0, agent.success_rate + 0.01)
            else:
                agent.success_rate = max(0.0, agent.success_rate - 0.05)

        # Deposit pheromones
        ptype = PheromoneSystem.SUCCESS if success else PheromoneSystem.FAILURE
        task_type = (
            task.required_capabilities[0] if task.required_capabilities else "general"
        )
        self.pheromones.deposit(
            ptype,
            f"task_type:{task_type}",
            agent_id=task.assigned_to or "",
        )

        logger.info(f"Task completed: {task_id}, success={success}")

        # The colony lives for the process while tasks arrive per request, so
        # finished entries must be bounded or the registries grow forever.
        self._prune_finished_tasks()

    # Retention cap for finished (completed/failed) tasks kept for stats and
    # self-heal inspection. Pending/in-progress tasks are never pruned.
    _FINISHED_TASK_RETENTION = 256

    def _prune_finished_tasks(self) -> None:
        """Evict the oldest finished tasks beyond the retention cap."""
        finished = [
            task_id
            for task_id, task in self._tasks.items()  # insertion-ordered
            if task.status in ("completed", "failed")
        ]
        excess = len(finished) - self._FINISHED_TASK_RETENTION
        for task_id in finished[:excess] if excess > 0 else []:
            self._tasks.pop(task_id, None)
            self._task_results.pop(task_id, None)
            self.auction.forget_task(task_id)

    async def request_help(
        self,
        agent_id: str,
        task_id: str,
        capabilities_needed: list[str] | None = None,
    ) -> str | None:
        """
        Agent requests help from the swarm.

        Args:
            agent_id: ID of requesting agent
            task_id: Task needing help
            capabilities_needed: Specific capabilities needed

        Returns:
            ID of helper agent, or None
        """
        task = self._tasks.get(task_id)
        if not task:
            return None

        # Deposit help_needed pheromone
        self.pheromones.deposit(
            PheromoneSystem.HELP_NEEDED,
            f"task:{task_id}",
            intensity=2.0,
            agent_id=agent_id,
        )

        # Find available helper
        for agent in self.get_available_agents():
            if agent.id == agent_id:
                continue

            if capabilities_needed:
                score = agent.get_capability_score(capabilities_needed)
                if score >= 0.5:
                    logger.info(f"Helper found: {agent.id} for task {task_id}")
                    return agent.id
            else:
                return agent.id

        return None

    async def handoff(
        self,
        from_agent: str,
        task_id: str,
        *,
        reason: str = "",
        to_agent: str | None = None,
        capabilities_needed: list[str] | None = None,
        context: dict[str, Any] | None = None,
        brief: "HandoffBrief | None" = None,
    ) -> Handoff | None:
        """Transfer a task from one agent to another with carried context.

        The structured counterpart to :meth:`request_help`: it not only finds a
        recipient but reassigns the task and emits a :class:`Handoff` carrying
        the sender's ``reason`` and accumulated ``context`` so the receiver
        continues the work instead of restarting it.

        Args:
            from_agent: ID of the agent handing the task off.
            task_id: The task being transferred.
            reason: Why the handoff is happening (e.g. "needs vision skill").
            to_agent: Explicit recipient; when omitted, the best available
                helper is selected via the same matching as ``request_help``.
            capabilities_needed: Capability filter used when auto-selecting.
            context: State/partial results to hand to the receiver. Oversized
                string values are truncated at the boundary
                (``compress_handoff_context``) so the payload stays bounded.
            brief: Optional :class:`HandoffBrief` — a structured summary
                (objective / facts / attempted / constraints) the receiver
                can splice into its prompt instead of parsing raw context.

        Returns:
            The recorded :class:`Handoff`, or None if the task is unknown or no
            recipient is available.
        """
        task = self._tasks.get(task_id)
        if not task:
            return None

        recipient: AgentProfile | None = None
        if to_agent and to_agent in self._agents:
            recipient = self._agents[to_agent]
        else:
            helper_id = await self.request_help(
                from_agent, task_id, capabilities_needed
            )
            recipient = self._agents.get(helper_id) if helper_id else None

        if recipient is None:
            logger.info("handoff_no_recipient task=%s from=%s", task_id, from_agent)
            return None

        refusal = self._handoff_guard.check(task_id, from_agent, recipient.id)
        if refusal is not None:
            logger.warning(
                "handoff_refused task=%s %s->%s: %s",
                task_id,
                from_agent,
                recipient.id,
                refusal,
            )
            return None

        task.assigned_to = recipient.id
        hop_count, visited = self._handoff_guard.record(task_id, from_agent)
        record = Handoff(
            task_id=task_id,
            from_agent=from_agent,
            to_agent=recipient.id,
            reason=reason,
            context=compress_handoff_context(context or {}),
            brief=brief,
            hop_count=hop_count,
            visited=visited,
        )
        # Notify subscribers via the existing message bus (directed by receiver_id).
        self.broadcast_message(record.to_message())
        logger.info(
            "handoff task=%s %s->%s reason=%s",
            task_id,
            from_agent,
            recipient.id,
            reason,
        )
        return record

    # ------------------------------------------------------------------
    # Batch execution
    # ------------------------------------------------------------------

    # Callback type: async (task, agent_profile) -> result
    ExecuteFn = Callable[["Task", "AgentProfile"], Awaitable[Any]]

    @dataclass
    class BatchResult:
        """Outcome of :meth:`Colony.execute_batch`."""

        completed: dict[str, Any] = field(default_factory=dict)
        failed: dict[str, str] = field(default_factory=dict)
        unassigned: list[str] = field(default_factory=list)

    async def execute_batch(
        self,
        tasks: list[Task],
        execute_fn: "Colony.ExecuteFn",
    ) -> "Colony.BatchResult":
        """
        Allocate and execute a collection of tasks in parallel.

        Orchestrates a multi-stage pipeline: batch auction allocation
        followed by concurrent execution using a provided callback function.
        Under an ambient checkpoint every auction outcome and every finished
        task's result is a recorded step, so a resumed batch neither
        re-auctions nor re-executes finished work (see
        :mod:`baselith.core.swarm.colony_batch`).

        Args:
            tasks: A list of tasks to be distributed and run.
            execute_fn: An async callable that handles the actual processing
                        for a single (task, agent) pair.

        Returns:
            BatchResult: A summary object containing successful results,
                        failure reasons, and unassigned task IDs.
        """
        with standalone_budget(batch_limits(self._loop_limits)):
            return await self._execute_batch(tasks, execute_fn)

    async def _execute_batch(
        self, tasks: list[Task], execute_fn: "Colony.ExecuteFn"
    ) -> "Colony.BatchResult":
        # Body in colony_batch (module size cap), durable under a checkpoint.
        from baselith.core.swarm.colony_batch import run_batch

        return await run_batch(self, tasks, execute_fn)

    def restore_assignment(self, task: Task, winner_id: str) -> None:
        """Reinstate an auction outcome recorded by an earlier pass.

        A resumed durable batch replays the recorded winner instead of running
        the auction again (see :mod:`baselith.core.swarm.colony_batch`); this
        puts the colony in the state :meth:`submit_task` would have left.
        """
        self._tasks[task.id] = task
        task.assigned_to = winner_id
        task.status = "assigned"
        if winner_id in self._agents:
            self._agents[winner_id].status = AgentStatus.BUSY
