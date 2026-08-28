"""
Task Scheduler

Schedule recurring tasks and manage task submission.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from rq.job import Job

from core.context import get_current_tenant_id
from core.observability.logging import get_logger
from core.task_queue import get_queue
from core.task_queue.status import TaskStatus, get_task_tracker

logger = get_logger(__name__)


@dataclass
class ScheduledTask:
    """Configuration for a scheduled task."""

    name: str
    func: Callable
    interval_seconds: int
    queue_name: str = "default"
    args: tuple = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    last_run: datetime | None = None
    enabled: bool = True


def ambient_job_meta() -> dict[str, Any]:
    """Identity + routing facts a queued job must carry with it.

    A job runs later, in another process, with none of the context its
    enqueuer had: no tenant, no plugin attribution, and — in a worker, which
    hosts no plugins — no per-plugin LLM policy resolver at all. Anything the
    work depends on has to travel *with* the work, or the same code silently
    behaves differently in the background than it does under a request.

    Carries the current tenant, the plugin the call runs on behalf of, and the
    LLM policy resolved for it (``core.services.llm.policy``). Best-effort by
    construction: a missing piece is simply absent from the metadata.
    """
    meta: dict[str, Any] = {}
    try:
        from core.context import get_current_plugin, get_current_tenant_id

        tenant_id = get_current_tenant_id()
        if tenant_id:
            meta["tenant_id"] = tenant_id
        plugin = get_current_plugin()
        if plugin:
            meta["plugin"] = plugin
    except Exception as exc:  # a metadata probe must never block enqueueing
        logger.debug(f"Ambient job context unavailable: {exc}")
    try:
        from core.services.llm.policy import (
            get_bound_llm_policy,
            policy_as_meta,
            resolve_active_llm_policy,
        )

        policy = policy_as_meta(resolve_active_llm_policy() or get_bound_llm_policy())
        if policy:
            meta["llm_policy"] = policy
    except Exception as exc:
        logger.debug(f"Ambient LLM policy unavailable: {exc}")
    return meta


def _merge_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Ambient context under an explicit *meta* — the caller always wins."""
    merged = ambient_job_meta()
    merged.update(meta or {})
    return merged


class TaskScheduler:
    """
    Submit tasks to RQ queues with retry configuration.

    For recurring tasks, use with an external scheduler like cron or APScheduler.
    """

    def __init__(self, redis_connection: Any | None = None, config: Any | None = None):
        """Initialize scheduler."""
        self._scheduled_tasks: dict[str, ScheduledTask] = {}
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
        job_timeout: int | None = None,
        result_ttl: int | None = None,
        failure_ttl: int | None = None,
        retry_count: int | None = None,
        retry_delay: int | None = None,
        meta: dict[str, Any] | None = None,
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
            meta=_merge_meta(meta),
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
        job_timeout: int | None = None,
        result_ttl: int | None = None,
        failure_ttl: int | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Schedule a task for execution at a specific time.

        Applies the same configured defaults as :meth:`enqueue`. Without
        this, scheduled jobs silently inherited RQ's own 180-second default
        timeout while immediately-enqueued ones got ``config.job_timeout``
        — so a job that ran fine when enqueued directly died with
        ``JobTimeoutException`` once something rescheduled it, and a
        self-chaining job (a simulation tick, a poll loop) hit that cliff on
        every run after the first.

        Args:
            func: The function to execute
            scheduled_time: When to execute
            *args: Positional arguments
            queue_name: Target queue
            job_timeout: Max execution time in seconds (config default)
            result_ttl: How long to keep results (config default)
            failure_ttl: How long to keep failed job info (config default)
            **kwargs: Keyword arguments

        Returns:
            Job ID
        """
        from core.config import get_task_queue_config

        config = get_task_queue_config()
        timeout = job_timeout if job_timeout is not None else config.job_timeout
        res_ttl = result_ttl if result_ttl is not None else config.result_ttl
        fail_ttl = failure_ttl if failure_ttl is not None else config.failure_ttl

        queue = get_queue(queue_name)

        job = queue.enqueue_at(
            scheduled_time,
            func,
            *args,
            job_timeout=timeout,
            result_ttl=res_ttl,
            failure_ttl=fail_ttl,
            meta=_merge_meta(kwargs.pop("meta", None)),
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

        Delegates to :meth:`enqueue_at`, so the configured ``job_timeout`` /
        TTL defaults apply here too (and ``job_timeout=`` can be passed
        through to bound one specific job).

        Args:
            func: The function to execute
            delay_seconds: Seconds to wait before execution
            *args: Positional arguments
            queue_name: Target queue
            **kwargs: Keyword arguments, including the ``enqueue_at``
                overrides (``job_timeout``, ``result_ttl``, ``failure_ttl``)

        Returns:
            Job ID
        """
        scheduled_time = datetime.now(UTC) + timedelta(seconds=delay_seconds)
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

    def get_job(self, job_id: str) -> dict[str, Any] | None:
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
_task_scheduler: TaskScheduler | None = None


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
