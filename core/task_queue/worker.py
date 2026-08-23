"""
Task Queue Worker.

Provides the background worker implementations that process enqueued tasks.
Includes multi-tenant context restoration to ensure correct isolated execution.

Two properties of this module are load-bearing and easy to lose:

* **The scheduler must be on.** ``TaskScheduler.enqueue_in``/``enqueue_at``
  park jobs in RQ's ``ScheduledJobRegistry``; a plain ``worker.work()`` never
  looks at that registry, so *delayed* jobs are accepted and then silently
  never run. Any producer that reschedules itself (a simulation tick chain,
  a retry-with-backoff) would execute exactly once and then stop. Every
  worker started here runs with ``with_scheduler=True``.
* **Failures must be durable.** Workers are built with
  ``dead_letter_handler`` so a terminally-failed job lands in the
  dead-letter queue instead of only RQ's TTL-bounded failed registry.
"""

import sys
from multiprocessing import Process

from redis import Redis
from rq import Queue, Worker

from core.config import get_task_queue_config
from core.context import (
    reset_tenant_context,
    reset_user_context,
    set_tenant_context,
    set_user_context,
)
from core.observability.logging import get_logger, redact_url_credentials

logger = get_logger(__name__)


class TenantAwareWorker(Worker):
    """
    Context-sensitive background processor.

    An RQ-based worker that automatically restores multi-tenant
    context (tenant_id) before executing background jobs. Ensures
    data isolation and correct configuration loading for asynchronous
    tasks.
    """

    def perform_job(self, job, queue):
        """Wraps job execution with tenant context."""
        tenant_id = job.meta.get("tenant_id", "default")
        user_id = job.meta.get("user_id")
        token = set_tenant_context(tenant_id)
        user_token = set_user_context(user_id) if user_id else None
        try:
            return super().perform_job(job, queue)
        finally:
            reset_tenant_context(token)
            if user_token is not None:
                reset_user_context(user_token)


def build_worker(queue_names: list[str], connection: Redis) -> TenantAwareWorker:
    """Create a tenant-aware worker wired to the dead-letter handler.

    Args:
        queue_names: Queues to listen on, in priority order.
        connection: Redis connection to the task-queue database.

    Returns:
        A worker ready to ``work()``.
    """
    from core.task_queue.dead_letter import dead_letter_handler

    queues = [Queue(name, connection=connection) for name in queue_names]
    return TenantAwareWorker(
        queues, connection=connection, exception_handlers=[dead_letter_handler]
    )


def run_worker(
    redis_url: str, queue_names: list[str], with_scheduler: bool = True
) -> None:
    """Run one worker in the current process until it is stopped.

    Module-level (not a closure) so it can be used as a
    :class:`multiprocessing.Process` target under both fork and spawn.

    Args:
        redis_url: Task-queue Redis URL.
        queue_names: Queues to listen on.
        with_scheduler: Also run RQ's scheduler, which is what promotes
            delayed/scheduled jobs into the queue. Leave on unless a
            dedicated scheduler process owns that job.
    """
    connection = Redis.from_url(redis_url)
    build_worker(queue_names, connection).work(with_scheduler=with_scheduler)


def start_worker(
    queue_names: list[str] | None = None,
    concurrency: int = 1,
    with_scheduler: bool = True,
) -> None:
    """Start ``concurrency`` workers listening on the configured queues.

    One worker runs in the calling process; any extra ones run as child
    processes and are joined on shutdown. Every worker runs the scheduler —
    RQ guards it with a Redis lock, so only one instance polls at a time.

    Args:
        queue_names: Queues to listen on. Defaults to the configured set.
        concurrency: Number of worker processes (minimum 1).
        with_scheduler: Whether workers also run RQ's scheduler.
    """
    config = get_task_queue_config()
    redis_url = config.get_redis_url()
    names = list(queue_names or config.queues)
    workers = max(1, concurrency)

    logger.info(f"Starting {workers} RQ worker(s) listening on: {names}")
    logger.info(f"Redis URL: {redact_url_credentials(redis_url)}")

    children: list[Process] = []
    for _ in range(workers - 1):
        child = Process(
            target=run_worker, args=(redis_url, names, with_scheduler), daemon=False
        )
        child.start()
        children.append(child)

    try:
        conn = Redis.from_url(redis_url)
        build_worker(names, conn).work(with_scheduler=with_scheduler)
    finally:
        for child in children:
            child.join()


if __name__ == "__main__":
    try:
        start_worker()
    except KeyboardInterrupt:
        print("\nExiting worker...")
        sys.exit(0)
