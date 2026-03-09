"""
Context-Aware Task Prioritization and Queue Management.

Implements intelligent resource scheduling for asynchronous job
execution. Dynamically re-orders tasks based on urgency, tenant
priority, and system load, ensuring that critical agent operations
are processed with optimal latency.
"""

import heapq
from core.observability.logging import get_logger
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from core.config.prioritization import PrioritizationConfig

from .models import Task, TaskStatus, DependencyGraph
from .scorer import TaskPrioritizer, PriorityScore

logger = get_logger(__name__)


class PriorityQueue:
    """
    Priority queue for task scheduling.

    Features:
    - Priority-based ordering
    - Dependency tracking
    - Dynamic reprioritization
    - Task lifecycle management
    """

    def __init__(
        self,
        prioritizer: Optional[TaskPrioritizer] = None,
        config: Optional["PrioritizationConfig"] = None,
    ):
        """
        Initialize priority queue.

        Args:
            prioritizer: TaskPrioritizer for scoring (uses default if None)
            config: Configuration object for default prioritizer
        """
        self.prioritizer = prioritizer or TaskPrioritizer(config=config)
        self.graph = DependencyGraph()
        self._heap: List[Tuple[float, str]] = []  # (-priority, task_id)
        self._scores: Dict[str, PriorityScore] = {}

    def enqueue(self, task: Task) -> PriorityScore:
        """
        Add task to queue.

        Args:
            task: Task to add

        Returns:
            Calculated priority score
        """
        self.graph.add_task(task)
        score = self._calculate_score(task)
        self._scores[task.id] = score

        # Only add to heap if ready (dependencies satisfied)
        if task.is_ready(self._get_completed_ids()):
            task.status = TaskStatus.READY
            heapq.heappush(self._heap, (-score.total, task.id))
        else:
            task.status = TaskStatus.BLOCKED

        logger.debug(f"Enqueued task {task.id} with priority {score.total:.3f}")
        return score

    def dequeue(self) -> Optional[Task]:
        """
        Get highest priority ready task.

        Returns:
            Task or None if queue empty
        """
        while self._heap:
            _, task_id = heapq.heappop(self._heap)
            task = self.graph.get_task(task_id)

            if task and task.status == TaskStatus.READY:
                task.status = TaskStatus.IN_PROGRESS
                logger.debug(f"Dequeued task {task_id}")
                return task

        return None

    def complete(self, task_id: str) -> List[Task]:
        """
        Mark task as completed and return newly ready tasks.

        Args:
            task_id: ID of completed task

        Returns:
            List of tasks that are now ready
        """
        newly_ready_ids = self.graph.mark_completed(task_id)
        newly_ready = []

        for ready_id in newly_ready_ids:
            task = self.graph.get_task(ready_id)
            if task:
                task.status = TaskStatus.READY
                score = self._calculate_score(task)
                self._scores[ready_id] = score
                heapq.heappush(self._heap, (-score.total, ready_id))
                newly_ready.append(task)

        logger.info(f"Completed task {task_id}, {len(newly_ready)} tasks now ready")
        return newly_ready

    def fail(self, task_id: str) -> None:
        """Mark task as failed."""
        task = self.graph.get_task(task_id)
        if task:
            task.status = TaskStatus.FAILED

    def reprioritize(
        self,
        task_id: str,
        urgency: Optional[float] = None,
        importance: Optional[float] = None,
    ) -> Optional[PriorityScore]:
        """
        Update task priority factors and recalculate score.

        Args:
            task_id: Task to reprioritize
            urgency: New urgency value (0-1)
            importance: New importance value (0-1)

        Returns:
            New priority score or None if task not found
        """
        task = self.graph.get_task(task_id)
        if not task:
            return None

        if urgency is not None:
            task.urgency = max(0.0, min(1.0, urgency))
        if importance is not None:
            task.importance = max(0.0, min(1.0, importance))

        new_score = self._calculate_score(task)
        self._scores[task_id] = new_score

        # Re-add to heap if ready (will be re-prioritized on next dequeue)
        if task.status == TaskStatus.READY:
            heapq.heappush(self._heap, (-new_score.total, task_id))

        logger.debug(f"Reprioritized task {task_id} to {new_score.total:.3f}")
        return new_score

    def get_score(self, task_id: str) -> Optional[PriorityScore]:
        """Get priority score for a task."""
        return self._scores.get(task_id)

    def get_ready_tasks(self) -> List[Tuple[Task, PriorityScore]]:
        """Get all ready tasks with their scores, sorted by priority."""
        ready = []
        for task in self.graph.get_ready_tasks():
            score = self._scores.get(task.id)
            if score:
                ready.append((task, score))

        return sorted(ready, key=lambda x: x[1].total, reverse=True)

    def _calculate_score(self, task: Task) -> PriorityScore:
        """Calculate priority score for a task."""
        dependent_count = len(self.graph.get_dependents(task.id))
        return self.prioritizer.score(task, dependent_count)

    def _get_completed_ids(self) -> set:
        """Get IDs of completed tasks."""
        return {
            t.id for t in self.graph._tasks.values() if t.status == TaskStatus.COMPLETED
        }

    def __len__(self) -> int:
        return len(
            [
                t
                for t in self.graph._tasks.values()
                if t.status
                in (TaskStatus.PENDING, TaskStatus.READY, TaskStatus.BLOCKED)
            ]
        )
