"""Colony maintenance and messaging operations.

Extracted from :mod:`core.swarm.colony` (module size cap) as a mixin: team
formation, message broadcast/registration, pheromone decay, stats, and the
self-healing routine. Behavior is unchanged — ``Colony`` mixes this in.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.swarm.types import AgentStatus, MessageType, SwarmMessage, Task

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class ColonyOpsMixin:
    """Maintenance/messaging surface shared into :class:`Colony`."""

    # Provided by Colony.
    _agents: dict[str, Any]
    _tasks: dict[str, Task]
    _handlers: dict[MessageType, list[Callable]]
    pheromones: Any
    team_engine: Any
    auction: Any
    config: Any

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
            "available_agents": len(self.get_available_agents()),  # type: ignore[attr-defined]
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


__all__ = ["ColonyOpsMixin"]
