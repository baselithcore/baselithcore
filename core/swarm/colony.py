"""
Swarm Colony Orchestration Module.

Acts as the central nervous system for swarm-based agentic workflows.
Coordinates decentralized task allocation, pheromone-based signaling,
and dynamic team formation to achieve emergent complex behaviors.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from core.config.swarm import SwarmConfig, get_swarm_config
from core.observability.logging import get_logger

from .auction import TaskAuction
from .pheromones import PheromoneSystem
from .team_formation import TeamFormationEngine
from .types import (
    AgentProfile,
    AgentStatus,
    Handoff,
    MessageType,
    SwarmMessage,
    Task,
)

if TYPE_CHECKING:
    from core.memory.manager import AgentMemory

logger = get_logger(__name__)


class Colony:
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
    ):
        """
        Initialize swarm colony.

        Args:
            config: Colony configuration
            auction: Optional auction system instance
            pheromones: Optional pheromone system instance
            team_engine: Optional team formation engine instance
            memory_manager: Optional memory orchestration manager
        """
        self.config = config or get_swarm_config()
        self.memory_manager = memory_manager

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
        winner_id = self.auction.resolve(task.id)

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
            context: State/partial results to hand to the receiver.

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

        task.assigned_to = recipient.id
        record = Handoff(
            task_id=task_id,
            from_agent=from_agent,
            to_agent=recipient.id,
            reason=reason,
            context=context or {},
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

    def form_team(self, task: Task, goal: str = "") -> str | None:
        """
        Form a team for complex task.

        Args:
            task: Task requiring team
            goal: Team goal

        Returns:
            Team ID, or None
        """
        team = self.team_engine.form_team(task, goal)
        return team.id if team else None

    def broadcast_message(self, message: SwarmMessage) -> None:
        """
        Broadcast message to all agents.

        Args:
            message: Message to broadcast
        """
        handlers = self._handlers.get(message.type, [])
        for handler in handlers:
            try:
                handler(message)
            except Exception as e:
                logger.error(f"Message handler error: {e}")

    def on_message(self, mtype: MessageType, handler: Callable) -> None:
        """
        Register a message handler.

        Args:
            mtype: Message type to handle
            handler: Handler function
        """
        if mtype not in self._handlers:
            self._handlers[mtype] = []
        self._handlers[mtype].append(handler)

    def decay_pheromones(self) -> None:
        """Trigger pheromone decay cycle."""
        self.pheromones.decay_all()

    def get_stats(self) -> dict:
        """Get colony statistics."""
        return {
            "total_agents": len(self._agents),
            "available_agents": len(self.get_available_agents()),
            "pending_tasks": len(
                [t for t in self._tasks.values() if t.status == "pending"]
            ),
            "completed_tasks": len(
                [t for t in self._tasks.values() if t.status == "completed"]
            ),
            "pheromones": self.pheromones.get_stats(),
            "active_teams": len(self.team_engine._teams),
        }

    def self_heal(self) -> None:
        """
        Self-healing routine for stuck tasks.

        Reassigns tasks from offline agents.
        """
        if not self.config.enable_auto_healing:
            return

        for task in self._tasks.values():
            if task.status == "assigned" and task.assigned_to:
                agent = self._agents.get(task.assigned_to)
                if agent and agent.status == AgentStatus.OFFLINE:
                    logger.warning(f"Reassigning task {task.id} from offline agent")
                    task.assigned_to = None
                    task.status = "pending"
                    # Re-submit
                    self.auction.announce_task(task)

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

        Args:
            tasks: A list of tasks to be distributed and run.
            execute_fn: An async callable that handles the actual processing
                        for a single (task, agent) pair.

        Returns:
            BatchResult: A summary object containing successful results,
                        failure reasons, and unassigned task IDs.
        """
        result = Colony.BatchResult()

        # 1. Allocate all tasks via auction
        allocations: list[tuple[Task, AgentProfile]] = []
        for task in tasks:
            winner_id = await self.submit_task(task)
            if winner_id and winner_id in self._agents:
                task.assigned_to = winner_id
                allocations.append((task, self._agents[winner_id]))
            else:
                result.unassigned.append(task.id)

        if not allocations:
            return result

        # A per-request LoopBudget breach is fatal to the whole batch: the
        # sub-agents share the ambient budget (inherited via the ContextVar in
        # core.orchestration.budget_context when each task is created), so once
        # it's exhausted no sibling can make progress. Let that (and cancellation)
        # propagate out of _run to abort the group; record ordinary per-task
        # failures instead of tearing down the batch.
        from core.orchestration.limits import BudgetExceededError

        # 2. Execute all allocated tasks concurrently under a TaskGroup so a
        #    fatal error deterministically cancels the siblings (structured
        #    concurrency), rather than leaving orphaned tasks running as
        #    ``gather`` could.
        async def _run(t: Task, agent: AgentProfile) -> tuple[str, bool, Any]:
            try:
                out = await execute_fn(t, agent)
                self.complete_task(t.id, success=True, result=out)
                return t.id, True, out
            except (asyncio.CancelledError, BudgetExceededError):
                self.complete_task(t.id, success=False)
                raise  # fatal → propagate to cancel the whole group
            except Exception as exc:
                self.complete_task(t.id, success=False)
                return t.id, False, str(exc)

        running: list[asyncio.Task[tuple[str, bool, Any]]] = []
        budget_error: BudgetExceededError | None = None
        try:
            async with asyncio.TaskGroup() as tg:
                running = [tg.create_task(_run(t, a)) for t, a in allocations]
        except* BudgetExceededError as eg:
            # The shared budget was exhausted mid-batch; siblings were cancelled
            # by the group. Capture the breach to re-raise after harvesting the
            # tasks that already finished.
            budget_error = eg.exceptions[0]  # type: ignore[assignment]

        for tt, (t, _agent) in zip(running, allocations, strict=True):
            if tt.cancelled():
                # Cancelled by the group when the budget breached — surface it
                # so the caller sees the task didn't complete.
                result.failed.setdefault(t.id, "cancelled: batch budget exceeded")
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
