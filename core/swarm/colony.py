"""
Swarm Colony Orchestration Module.

Acts as the central nervous system for swarm-based agentic workflows.
Coordinates decentralized task allocation, pheromone-based signaling,
and dynamic team formation to achieve emergent complex behaviors.
"""

import asyncio
from core.observability.logging import get_logger
from dataclasses import dataclass, field
from typing import Awaitable, Dict, List, Optional, Callable, Any

from core.config.swarm import SwarmConfig, get_swarm_config
from .types import AgentProfile, Task, SwarmMessage, MessageType, AgentStatus
from .auction import TaskAuction
from .pheromones import PheromoneSystem
from .team_formation import TeamFormationEngine

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
        config: Optional[SwarmConfig] = None,
        auction: Optional[TaskAuction] = None,
        pheromones: Optional[PheromoneSystem] = None,
        team_engine: Optional[TeamFormationEngine] = None,
    ):
        """
        Initialize swarm colony.

        Args:
            config: Colony configuration
            auction: Optional auction system instance
            pheromones: Optional pheromone system instance
            team_engine: Optional team formation engine instance
        """
        self.config = config or get_swarm_config()

        # Core subsystems
        self.auction = auction or TaskAuction(config=self.config.auction)
        self.pheromones = pheromones or PheromoneSystem(
            decay_rate=self.config.pheromone_decay_rate
        )
        self.team_engine = team_engine or TeamFormationEngine(config=self.config.team)

        # Agent registry
        self._agents: Dict[str, AgentProfile] = {}

        # Task tracking
        self._tasks: Dict[str, Task] = {}
        self._task_results: Dict[str, Any] = {}

        # Message handlers
        self._handlers: Dict[MessageType, List[Callable]] = {}

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

    def get_agent(self, agent_id: str) -> Optional[AgentProfile]:
        """Get agent by ID."""
        return self._agents.get(agent_id)

    def get_available_agents(self) -> List[AgentProfile]:
        """Get all available agents."""
        return [a for a in self._agents.values() if a.is_available]

    async def submit_task(self, task: Task) -> Optional[str]:
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
        result: Optional[Any] = None,
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
        capabilities_needed: Optional[List[str]] = None,
    ) -> Optional[str]:
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

    def form_team(self, task: Task, goal: str = "") -> Optional[str]:
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

    def get_stats(self) -> Dict:
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

        completed: Dict[str, Any] = field(default_factory=dict)
        failed: Dict[str, str] = field(default_factory=dict)
        unassigned: List[str] = field(default_factory=list)

    async def execute_batch(
        self,
        tasks: List[Task],
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
        allocations: List[tuple[Task, AgentProfile]] = []
        for task in tasks:
            winner_id = await self.submit_task(task)
            if winner_id and winner_id in self._agents:
                task.assigned_to = winner_id
                allocations.append((task, self._agents[winner_id]))
            else:
                result.unassigned.append(task.id)

        if not allocations:
            return result

        # 2. Execute all allocated tasks concurrently
        async def _run(t: Task, agent: AgentProfile) -> tuple[str, bool, Any]:
            try:
                out = await execute_fn(t, agent)
                self.complete_task(t.id, success=True, result=out)
                return t.id, True, out
            except asyncio.CancelledError:
                self.complete_task(t.id, success=False)
                raise
            except Exception as exc:
                self.complete_task(t.id, success=False)
                return t.id, False, str(exc)

        outcomes = await asyncio.gather(
            *(_run(t, a) for t, a in allocations),
            return_exceptions=False,
        )

        for task_id, ok, payload in outcomes:
            if ok:
                result.completed[task_id] = payload
            else:
                result.failed[task_id] = payload

        return result
