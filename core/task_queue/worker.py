"""
Task Queue Worker.

Provides the background worker implementations that process enqueued tasks.
Includes multi-tenant context restoration to ensure correct isolated execution.
"""

from core.observability.logging import get_logger
import sys
from redis import Redis
from rq import Worker, Queue
from core.config import get_task_queue_config
from core.context import (
    set_tenant_context,
    reset_tenant_context,
    set_user_context,
    reset_user_context,
)

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


def start_worker():
    """Starts an RQ worker listening on specified queues."""
    config = get_task_queue_config()

    # Use dedicated queue Redis URL with fallback
    redis_url = config.get_redis_url()
    conn = Redis.from_url(redis_url)

    listen_queues = config.queues

    queues = [Queue(name, connection=conn) for name in listen_queues]
    # Record terminally-failed jobs to the durable dead-letter queue (in addition
    # to RQ's default FailedJobRegistry handling).
    from core.task_queue.dead_letter import dead_letter_handler

    worker = TenantAwareWorker(
        queues, connection=conn, exception_handlers=[dead_letter_handler]
    )

    logger.info(f"Starting RQ Worker listening on: {listen_queues}")
    logger.info(f"Redis URL: {redis_url}")
    worker.work()


if __name__ == "__main__":
    try:
        start_worker()
    except KeyboardInterrupt:
        print("\nExiting worker...")
        sys.exit(0)
