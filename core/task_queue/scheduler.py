"""
Task Scheduler

Schedule recurring tasks and manage task submission.
"""

from core.observability.logging import get_logger
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional
from dataclasses import dataclass, field

from rq.job import Job

from core.task_queue import get_queue
from core.task_queue.status import get_task_tracker, TaskStatus
from core.context import get_current_tenant_id

logger = get_logger(__name__)


@dataclass
class ScheduledTask:
    """Configuration for a scheduled task."""

    name: str
    func: Callable
    interval_seconds: int
    queue_name: str = "default"
    args: tuple = field(default_factory=tuple)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    last_run: Optional[datetime] = None
    enabled: bool = True


class TaskScheduler:
    """
    Submit tasks to RQ queues with retry configuration.

    For recurring tasks, use with an external scheduler like cron or APScheduler.
    """

    def __init__(
        self, redis_connection: Optional[Any] = None, config: Optional[Any] = None
    ):
        """Initialize scheduler."""
        self._scheduled_tasks: Dict[str, ScheduledTask] = {}
        # We don't necessarily need to store redis_conn if get_queue() handles it,
        # but for clean DI we'll store what we need or just access context helpers.
        # Ideally, we should inject everything.

        # NOTE: get_queue will handle connection retrieval internally in this refactor,
        # so we don't strictly need to store redis_connection here UNLESS we want to avoid the import.
        # But to keep it clean, we'll let get_queue do its job.

    def enqueue(
        self,
        func: Callable,
        *args: Any,
        queue_name: str = "default",
        job_timeout: Optional[int] = None,
        result_ttl: Optional[int] = None,
        failure_ttl: Optional[int] = None,
        retry_count: Optional[int] = None,
        retry_delay: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> str:
        """
        Enqueue a task for execution.

        Args:
            func: The function to execute
            *args: Positional arguments for the function
            queue_name: Target queue (default, documents, analysis)
            job_timeout: Max execution time in seconds
            result_ttl: How long to keep results (seconds)
            failure_ttl: How long to keep failed job info (seconds)
            retry_count: Number of retries on failure
            retry_delay: Delay between retries (seconds) - NOTE: RQ standard retry doesn't support delay easily in simple enqueue
            meta: Optional metadata to attach to job
            **kwargs: Keyword arguments for the function

        Returns:
            Job ID
        """
        from core.config import get_task_queue_config

        config = get_task_queue_config()

        # Apply defaults from config if not specified
        timeout = job_timeout if job_timeout is not None else config.job_timeout
        res_ttl = result_ttl if result_ttl is not None else config.result_ttl
        fail_ttl = failure_ttl if failure_ttl is not None else config.failure_ttl
        retries = retry_count if retry_count is not None else config.default_retry_count

        queue = get_queue(queue_name)

        # Build RQ Retry object (rq expects Retry, not a plain int)
        retry_config = None
        if retries and retries > 0:
            from rq import Retry

            retry_config = Retry(max=retries)

        job = queue.enqueue(
            func,
            *args,
            job_timeout=timeout,
            result_ttl=res_ttl,
            failure_ttl=fail_ttl,
            retry=retry_config,
            meta=meta or {},
            **kwargs,
        )

        # Initialize task status
        get_task_tracker().set_status(
            job.id,
            TaskStatus.QUEUED,
            message=f"Queued in {queue_name}",
        )

        logger.info(f"Enqueued task {func.__name__} -> job {job.id}")
        return job.id

    def enqueue_at(
        self,
        func: Callable,
        scheduled_time: datetime,
        *args: Any,
        queue_name: str = "default",
        **kwargs: Any,
    ) -> str:
        """
        Schedule a task for execution at a specific time.

        Args:
            func: The function to execute
            scheduled_time: When to execute
            *args: Positional arguments
            queue_name: Target queue
            **kwargs: Keyword arguments

        Returns:
            Job ID
        """
        queue = get_queue(queue_name)

        job = queue.enqueue_at(
            scheduled_time,
            func,
            *args,
            **kwargs,
        )

        get_task_tracker().set_status(
            job.id,
            TaskStatus.PENDING,
            message=f"Scheduled for {scheduled_time.isoformat()}",
        )

        logger.info(f"Scheduled task {func.__name__} for {scheduled_time}")
        return job.id

    def enqueue_in(
        self,
        func: Callable,
        delay_seconds: int,
        *args: Any,
        queue_name: str = "default",
        **kwargs: Any,
    ) -> str:
        """
        Schedule a task to run after a delay.

        Args:
            func: The function to execute
            delay_seconds: Seconds to wait before execution
            *args: Positional arguments
            queue_name: Target queue
            **kwargs: Keyword arguments

        Returns:
            Job ID
        """
        scheduled_time = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        return self.enqueue_at(
            func, scheduled_time, *args, queue_name=queue_name, **kwargs
        )

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a pending job.

        Returns:
            True if job was cancelled
        """
        try:
            from core.task_queue import get_queue_redis_connection

            conn = get_queue_redis_connection()
            job = Job.fetch(job_id, connection=conn)
            job.cancel()
            get_task_tracker().set_status(
                job_id, TaskStatus.CANCELLED, message="Cancelled by user"
            )
            logger.info(f"Cancelled job: {job_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")
            return False

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get job details.

        Returns:
            Job info dict or None
        """
        try:
            from core.task_queue import get_queue_redis_connection

            conn = get_queue_redis_connection()
            job = Job.fetch(job_id, connection=conn)
            return {
                "id": job.id,
                "status": job.get_status(),
                "func_name": job.func_name,
                "origin": job.origin,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "ended_at": job.ended_at.isoformat() if job.ended_at else None,
                "result": job.result,
                "meta": job.meta,
            }
        except Exception:
            return None


# Global scheduler instance (lazy)
_task_scheduler: Optional[TaskScheduler] = None


def get_task_scheduler() -> TaskScheduler:
    """Get the global task scheduler instance."""
    global _task_scheduler
    if _task_scheduler is None:
        _task_scheduler = TaskScheduler()
    return _task_scheduler


# For backward compatibility
def __getattr__(name: str) -> Any:
    if name == "task_scheduler":
        return get_task_scheduler()
    raise AttributeError(f"module {__name__} has no attribute {name}")


# Convenience functions
def enqueue_task(
    func: Callable,
    *args: Any,
    queue: str = "default",
    **kwargs: Any,
) -> str:
    """
    Enqueue a task for immediate execution.
    Injects the current tenant_id into the job metadata.
    """
    # Capture current tenant
    tenant_id = get_current_tenant_id()

    # Extract meta from kwargs if present, otherwise initialize
    meta = kwargs.pop("meta", {})
    meta["tenant_id"] = tenant_id

    job_id = get_task_scheduler().enqueue(
        func, *args, queue_name=queue, meta=meta, **kwargs
    )
    logger.info(f"Enqueued task {func.__name__} -> job {job_id} (tenant={tenant_id})")
    return job_id


def schedule_task(
    func: Callable,
    delay_seconds: int,
    *args: Any,
    queue: str = "default",
    **kwargs: Any,
) -> str:
    """Schedule a task to run after a delay."""
    return get_task_scheduler().enqueue_in(
        func, delay_seconds, *args, queue_name=queue, **kwargs
    )
