"""
Goal Tracker

Tracks goals and their progress.
"""

from core.observability.logging import get_logger
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

logger = get_logger(__name__)


class GoalStatus(Enum):
    """Goal status."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass
class Goal:
    """A tracked goal with progress metrics."""

    id: str
    title: str
    description: str = ""
    status: GoalStatus = GoalStatus.NOT_STARTED
    progress: float = 0.0  # 0.0 to 1.0

    # Success criteria
    # List of criteria descriptions
    success_criteria: List[str] = field(default_factory=list)
    # Map of criteria description to met status
    criteria_met: Dict[str, bool] = field(default_factory=dict)

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Metadata
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self):
        """Initialize criteria_met if not matching success_criteria."""
        for criterion in self.success_criteria:
            if criterion not in self.criteria_met:
                self.criteria_met[criterion] = False

    @property
    def is_complete(self) -> bool:
        """Check if goal is complete."""
        return self.status == GoalStatus.COMPLETED

    def update_progress(self, value: float) -> None:
        """Update progress (0.0 to 1.0)."""
        self.progress = max(0.0, min(1.0, value))
        if self.status == GoalStatus.NOT_STARTED and self.progress > 0:
            self.status = GoalStatus.IN_PROGRESS
            self.started_at = datetime.now(timezone.utc)


class GoalTracker:
    """
    Tracks multiple goals and their progress.

    Features:
    - Goal registration
    - Progress updates
    - Success validation
    """

    def __init__(self):
        """Initialize the Goal Tracker service."""
        self._goals: Dict[str, Goal] = {}

    async def add(self, goal: Goal) -> None:
        """Add a goal to track."""
        self._goals[goal.id] = goal
        logger.info(f"Added goal: {goal.id} - {goal.title}")

    def get(self, goal_id: str) -> Optional[Goal]:
        """Get goal by ID."""
        return self._goals.get(goal_id)

    async def update_progress(self, goal_id: str, progress: float) -> bool:
        """Update goal progress. Returns False if not found."""
        goal = self._goals.get(goal_id)
        if goal:
            goal.update_progress(progress)
            logger.info(f"Updated progress for goal {goal_id} to {progress}")
            return True
        logger.warning(f"Attempted to update progress for unknown goal: {goal_id}")
        return False

    async def complete(self, goal_id: str) -> bool:
        """Mark goal as completed."""
        goal = self._goals.get(goal_id)
        if goal:
            goal.status = GoalStatus.COMPLETED
            goal.progress = 1.0
            goal.completed_at = datetime.now(timezone.utc)
            logger.info(f"Goal completed: {goal_id}")
            return True
        logger.warning(f"Attempted to complete unknown goal: {goal_id}")
        return False

    async def fail(self, goal_id: str, reason: str = "") -> bool:
        """Mark goal as failed."""
        goal = self._goals.get(goal_id)
        if goal:
            goal.status = GoalStatus.FAILED
            goal.metadata["failure_reason"] = reason
            logger.info(f"Goal failed: {goal_id}. Reason: {reason}")
            return True
        logger.warning(f"Attempted to fail unknown goal: {goal_id}")
        return False

    def validate_criteria(self, goal_id: str) -> bool:
        """Check if all success criteria are met."""
        goal = self._goals.get(goal_id)
        if not goal or not goal.success_criteria:
            return False

        # Check if all defined criteria are marked as True in criteria_met
        return all(goal.criteria_met.get(c, False) for c in goal.success_criteria)

    def get_active(self) -> List[Goal]:
        """Get all active (in-progress) goals."""
        return [g for g in self._goals.values() if g.status == GoalStatus.IN_PROGRESS]

    def get_summary(self) -> Dict:
        """Get summary of all goals."""
        return {
            "total": len(self._goals),
            "completed": sum(1 for g in self._goals.values() if g.is_complete),
            "in_progress": sum(
                1 for g in self._goals.values() if g.status == GoalStatus.IN_PROGRESS
            ),
            "failed": sum(
                1 for g in self._goals.values() if g.status == GoalStatus.FAILED
            ),
        }
