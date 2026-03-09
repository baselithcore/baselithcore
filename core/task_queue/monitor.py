"""
Worker Health Monitoring

Monitor RQ worker health and provide statistics.
"""

from core.observability.logging import get_logger
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from redis import Redis
from rq import Worker, Queue
from rq.job import Job


logger = get_logger(__name__)


@dataclass
class WorkerInfo:
    """Information about an RQ worker."""

    name: str
    state: str
    queues: List[str]
    current_job: Optional[str]
    successful_jobs: int
    failed_jobs: int
    birth_date: Optional[datetime]
    last_heartbeat: Optional[datetime]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "state": self.state,
            "queues": self.queues,
            "current_job": self.current_job,
            "successful_jobs": self.successful_jobs,
            "failed_jobs": self.failed_jobs,
            "birth_date": self.birth_date.isoformat() if self.birth_date else None,
            "last_heartbeat": (
                self.last_heartbeat.isoformat() if self.last_heartbeat else None
            ),
        }


@dataclass
class QueueInfo:
    """Information about an RQ queue."""

    name: str
    job_count: int
    started_job_count: int
    deferred_job_count: int
    finished_job_count: int
    failed_job_count: int

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "job_count": self.job_count,
            "started_job_count": self.started_job_count,
            "deferred_job_count": self.deferred_job_count,
            "finished_job_count": self.finished_job_count,
            "failed_job_count": self.failed_job_count,
        }


class WorkerMonitor:
    """
    Monitor RQ workers and queues.

    Provides health checks and statistics.
    """

    def __init__(self, conn: Optional[Redis] = None):
        """Initialize monitor."""
        from core.task_queue import get_queue_redis_connection

        self._conn = conn or get_queue_redis_connection()

    def get_workers(self) -> List[WorkerInfo]:
        """Get all active workers."""
        workers = Worker.all(connection=self._conn)
        return [
            WorkerInfo(
                name=w.name,
                state=w.get_state(),
                queues=[q.name for q in w.queues],
                current_job=w.get_current_job_id(),
                successful_jobs=w.successful_job_count,
                failed_jobs=w.failed_job_count,
                birth_date=w.birth_date,
                last_heartbeat=w.last_heartbeat,
            )
            for w in workers
        ]

    def get_worker_count(self) -> int:
        """Get number of active workers."""
        return Worker.count(connection=self._conn)

    def get_queue_info(self, queue_name: str) -> Optional[QueueInfo]:
        """Get information about a specific queue."""
        try:
            queue = Queue(queue_name, connection=self._conn)
            return QueueInfo(
                name=queue.name,
                job_count=len(queue),
                started_job_count=queue.started_job_registry.count,
                deferred_job_count=queue.deferred_job_registry.count,
                finished_job_count=queue.finished_job_registry.count,
                failed_job_count=queue.failed_job_registry.count,
            )
        except Exception as e:
            logger.error(f"Error getting queue info: {e}")
            return None

    def get_all_queues(self) -> List[QueueInfo]:
        """Get info for all known queues."""
        from core.config import get_task_queue_config

        config = get_task_queue_config()
        queue_names = config.queues
        queues = []
        for name in queue_names:
            info = self.get_queue_info(name)
            if info:
                queues.append(info)
        return queues

    def get_health_status(self) -> Dict[str, Any]:
        """
        Get overall health status of the task queue system.

        Returns:
            Dict with health check results
        """
        try:
            # Check Redis connectivity
            self._conn.ping()
            redis_ok = True
        except Exception:
            redis_ok = False

        workers = self.get_workers()
        queues = self.get_all_queues()

        total_pending = sum(q.job_count for q in queues)
        total_failed = sum(q.failed_job_count for q in queues)
        active_workers = len([w for w in workers if w.state == "busy"])

        # Determine overall health
        if not redis_ok:
            status = "unhealthy"
            message = "Redis connection failed"
        elif len(workers) == 0:
            status = "degraded"
            message = "No workers available"
        elif total_failed > 100:
            status = "degraded"
            message = f"High failure count: {total_failed}"
        else:
            status = "healthy"
            message = "All systems operational"

        return {
            "status": status,
            "message": message,
            "redis_connected": redis_ok,
            "workers": {
                "total": len(workers),
                "active": active_workers,
                "idle": len(workers) - active_workers,
            },
            "queues": {
                "total_pending": total_pending,
                "total_failed": total_failed,
                "details": [q.to_dict() for q in queues],
            },
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def clean_failed_jobs(self, queue_name: str = "default") -> int:
        """
        Clean failed jobs from a queue.

        Returns:
            Number of jobs cleaned
        """
        queue = Queue(queue_name, connection=self._conn)
        registry = queue.failed_job_registry
        job_ids = registry.get_job_ids()

        count = 0
        for job_id in job_ids:
            try:
                registry.remove(job_id)
                count += 1
            except Exception as e:
                logger.warning(f"Failed to remove job {job_id}: {e}")

        logger.info(f"Cleaned {count} failed jobs from {queue_name}")
        return count

    def retry_failed_job(self, job_id: str) -> bool:
        """
        Retry a specific failed job.

        Returns:
            True if job was successfully requeued
        """
        try:
            job = Job.fetch(job_id, connection=self._conn)
            job.requeue()
            logger.info(f"Requeued job: {job_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to retry job {job_id}: {e}")
            return False


# Global monitor instance (lazy)
_worker_monitor: Optional[WorkerMonitor] = None


def get_worker_monitor() -> WorkerMonitor:
    """Get the global worker monitor instance."""
    global _worker_monitor
    if _worker_monitor is None:
        _worker_monitor = WorkerMonitor()
    return _worker_monitor


# For backward compatibility
def __getattr__(name: str) -> Any:
    if name == "worker_monitor":
        return get_worker_monitor()
    raise AttributeError(f"module {__name__} has no attribute {name}")
