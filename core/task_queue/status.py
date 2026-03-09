"""
Task Status Tracking

Provides task status monitoring and persistence for RQ jobs.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
from datetime import datetime, timezone

from redis import Redis
from rq import get_current_job
from rq.job import Job


class TaskStatus(str, Enum):
    """Task execution status."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    """Task metadata and status information."""

    id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    queue: str = "default"
    progress: float = 0.0
    message: str = ""
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "queue": self.queue,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "metadata": self.metadata,
        }


class TaskTracker:
    """
    Track task status in Redis.

    Provides real-time status updates and progress tracking.
    """

    PREFIX = "task:status:"

    def __init__(self, conn: Redis, ttl: int = 604800):
        """Initialize tracker with Redis connection."""
        self._conn = conn
        self.ttl = ttl

    def _key(self, task_id: str) -> str:
        """Get Redis key for task."""
        return f"{self.PREFIX}{task_id}"

    def set_status(
        self,
        task_id: str,
        status: TaskStatus,
        progress: float = 0.0,
        message: str = "",
        result: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update task status."""
        import json

        data: Dict[str | bytes, bytes | float | int | str] = {
            "status": status.value,
            "progress": progress,
            "message": message,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if result is not None:
            data["result"] = json.dumps(result)
        if error is not None:
            data["error"] = error

        self._conn.hset(self._key(task_id), mapping=data)
        self._conn.expire(self._key(task_id), self.ttl)

    def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task status."""
        import json

        # Mypy thinks hgetall might return Awaitable, but we are using sync Redis here
        data: Dict[Any, Any] = self._conn.hgetall(self._key(task_id))  # type: ignore
        if not data:
            return None

        result = {}
        for k, v in data.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            if key == "result":
                try:
                    result[key] = json.loads(val)
                except Exception:
                    result[key] = val
            elif key == "progress":
                result[key] = float(val)
            else:
                result[key] = val
        return result

    def mark_started(self, task_id: str, message: str = "Task started") -> None:
        """Mark task as started."""
        self.set_status(task_id, TaskStatus.RUNNING, progress=0.0, message=message)

    def update_progress(
        self,
        task_id: str,
        progress: float,
        message: str = "",
    ) -> None:
        """Update task progress."""
        self.set_status(task_id, TaskStatus.RUNNING, progress=progress, message=message)

    def mark_completed(
        self,
        task_id: str,
        result: Optional[Any] = None,
        message: str = "Task completed",
    ) -> None:
        """Mark task as completed."""
        self.set_status(
            task_id,
            TaskStatus.COMPLETED,
            progress=100.0,
            message=message,
            result=result,
        )

    def mark_failed(self, task_id: str, error: str) -> None:
        """Mark task as failed."""
        self.set_status(task_id, TaskStatus.FAILED, message="Task failed", error=error)


# Lazy singleton instance
_task_tracker: Optional[TaskTracker] = None


def get_task_tracker() -> TaskTracker:
    """Get the global task tracker instance."""
    global _task_tracker
    if _task_tracker is None:
        from core.task_queue import get_queue_redis_connection
        from core.config import get_task_queue_config

        conn = get_queue_redis_connection()
        config = get_task_queue_config()
        _task_tracker = TaskTracker(conn, ttl=config.failure_ttl)
    return _task_tracker


# For backward compatibility
def __getattr__(name: str) -> Any:
    if name == "task_tracker":
        return get_task_tracker()
    raise AttributeError(f"module {__name__} has no attribute {name}")


def update_job_progress(progress: float, message: str = "") -> None:
    """
    Update progress of currently executing RQ job.

    Use this inside task functions to report progress.

    Example:
        def my_task():
            update_job_progress(25, "Processing step 1")
            # ... work ...
            update_job_progress(50, "Processing step 2")
    """
    job = get_current_job()
    if job:
        get_task_tracker().update_progress(job.id, progress, message)


def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    """
    Get combined RQ job and tracker status.

    Returns RQ job info merged with tracker progress data.
    """
    from core.task_queue import get_queue_redis_connection

    conn = get_queue_redis_connection()

    try:
        job = Job.fetch(job_id, connection=conn)
    except Exception:
        job = None

    tracker_status = get_task_tracker().get_status(job_id)

    if not job and not tracker_status:
        return None

    result: Dict[str, Any] = {"id": job_id}

    if job:
        result.update(
            {
                "rq_status": job.get_status(),
                "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "ended_at": job.ended_at.isoformat() if job.ended_at else None,
                "func_name": job.func_name,
                "origin": job.origin,
            }
        )

    if tracker_status:
        result.update(tracker_status)

    return result
