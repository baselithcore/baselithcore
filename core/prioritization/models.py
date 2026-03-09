"""
Prioritization Models

Data models for task prioritization.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Set


class TaskStatus(Enum):
    """Task execution status."""

    PENDING = "pending"
    READY = "ready"  # Dependencies resolved
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"  # Waiting for dependencies


@dataclass
class Task:
    """Task with priority metadata."""

    id: str
    name: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING

    # Priority factors
    urgency: float = 0.5  # 0.0 to 1.0
    importance: float = 0.5  # 0.0 to 1.0
    effort: float = 0.5  # 0.0 to 1.0 (lower = less effort)

    # Dependencies
    dependencies: List[str] = field(default_factory=list)  # Task IDs

    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    deadline: Optional[datetime] = None

    # Metadata
    tags: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    def is_ready(self, completed_tasks: Set[str]) -> bool:
        """Check if all dependencies are satisfied."""
        return all(dep in completed_tasks for dep in self.dependencies)


class DependencyGraph:
    """Manages task dependencies."""

    def __init__(self):
        """Initialize an empty dependency graph."""
        self._tasks: Dict[str, Task] = {}
        self._dependents: Dict[str, Set[str]] = {}  # task_id -> tasks that depend on it

    def add_task(self, task: Task) -> None:
        """Add task to graph."""
        self._tasks[task.id] = task

        # Track reverse dependencies
        for dep_id in task.dependencies:
            if dep_id not in self._dependents:
                self._dependents[dep_id] = set()
            self._dependents[dep_id].add(task.id)

    def remove_task(self, task_id: str) -> Optional[Task]:
        """Remove task from graph."""
        task = self._tasks.pop(task_id, None)
        if task:
            # Clean up dependent tracking
            for dep_id in task.dependencies:
                if dep_id in self._dependents:
                    self._dependents[dep_id].discard(task_id)
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        return self._tasks.get(task_id)

    def get_ready_tasks(self) -> List[Task]:
        """Get all tasks whose dependencies are satisfied."""
        completed = {
            tid for tid, t in self._tasks.items() if t.status == TaskStatus.COMPLETED
        }

        return [
            task
            for task in self._tasks.values()
            if task.status == TaskStatus.PENDING and task.is_ready(completed)
        ]

    def get_dependents(self, task_id: str) -> Set[str]:
        """Get tasks that depend on the given task."""
        return self._dependents.get(task_id, set())

    def mark_completed(self, task_id: str) -> List[str]:
        """
        Mark task as completed and return newly unblocked tasks.

        Returns:
            List of task IDs that are now ready
        """
        task = self._tasks.get(task_id)
        if not task:
            return []

        task.status = TaskStatus.COMPLETED

        # Check which dependents are now ready
        completed = {
            tid for tid, t in self._tasks.items() if t.status == TaskStatus.COMPLETED
        }

        newly_ready = []
        for dep_id in self._dependents.get(task_id, set()):
            dep_task = self._tasks.get(dep_id)
            # Check both PENDING and BLOCKED status (BLOCKED = waiting for dependencies)
            if dep_task and dep_task.status in (TaskStatus.PENDING, TaskStatus.BLOCKED):
                if dep_task.is_ready(completed):
                    newly_ready.append(dep_id)

        return newly_ready

    def has_cycle(self) -> bool:
        """Check if dependency graph has cycles."""
        visited = set()
        rec_stack = set()

        def dfs(task_id: str) -> bool:
            """
            Depth-first search to detect cycles.

            Args:
                task_id: The ID of the task to start checking from.

            Returns:
                True if a cycle is detected, False otherwise.
            """
            visited.add(task_id)
            rec_stack.add(task_id)

            task = self._tasks.get(task_id)
            if task:
                for dep_id in task.dependencies:
                    if dep_id not in visited:
                        if dfs(dep_id):
                            return True
                    elif dep_id in rec_stack:
                        return True

            rec_stack.discard(task_id)
            return False

        for task_id in self._tasks:
            if task_id not in visited:
                if dfs(task_id):
                    return True

        return False

    def topological_sort(self) -> List[str]:
        """Get tasks in topological order."""
        visited = set()
        result = []

        def dfs(task_id: str):
            """
            Depth-first search to build topological order.

            Args:
                task_id: The ID of the task to visit.
            """
            if task_id in visited:
                return
            visited.add(task_id)

            task = self._tasks.get(task_id)
            if task:
                for dep_id in task.dependencies:
                    dfs(dep_id)

            result.append(task_id)

        for task_id in self._tasks:
            dfs(task_id)

        return result

    def __len__(self) -> int:
        return len(self._tasks)
