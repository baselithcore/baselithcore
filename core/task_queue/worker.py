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
* **A job keeps the identity it was enqueued with.** Tenant, user, owning
  plugin and the plugin's pinned LLM policy are restored from the job's
  metadata before it runs. A worker hosts no plugins, so without this a
  plugin pinned to one provider had its HTTP calls served by that provider
  and its background work by the deployment default — the same plugin
  answering from two different models, with nothing on screen to say so.
"""

import os
import sys
from contextlib import AbstractContextManager, nullcontext
from multiprocessing import Process
from typing import Any

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
from core.task_queue.trace_context import consumer_span

logger = get_logger(__name__)


def _rls_enabled() -> bool:
    """Whether row-level security is switched on for this deployment."""
    try:
        from core.db.connection import DB_RLS_ENABLED
    except ImportError:  # pragma: no cover - depends on the installed core
        return False
    return bool(DB_RLS_ENABLED)


def _system_tenant_scope() -> AbstractContextManager[Any] | None:
    """``core.db.connection.system_tenant_scope()``, when this core has it.

    Imported lazily and guarded: the helper is a newer addition, and a worker
    must keep draining its queue on a core that predates it.
    """
    try:
        from core.db.connection import system_tenant_scope
    except ImportError:  # pragma: no cover - depends on the installed core
        return None
    return system_tenant_scope()


def _tenant_for(
    meta: dict[str, Any],
) -> tuple[str | None, AbstractContextManager | None]:
    """Resolve how to bind identity for a job, from its metadata.

    Returns ``(tenant_id, system_scope)`` — exactly one is set.

    A job that names a tenant simply gets it. A job that names none is
    out-of-request work, and what that should mean depends on RLS:

    * **RLS off** — nothing reads ``app.tenant_id`` for access control, so the
      historical ``"default"`` fallback is kept. Switching such a job to a new
      tenant id would silently move its cache and memory namespaces.
    * **RLS on** — ``"default"`` is another tenant's rows, and
      ``_current_tenant_for_session`` refuses to invent a tenant at all, so the
      job needs an explicit identity: ``system_tenant_scope()``.
    """
    tenant_id = meta.get("tenant_id")
    if tenant_id:
        return str(tenant_id), None
    scope = _system_tenant_scope() if _rls_enabled() else None
    return (None, scope) if scope is not None else ("default", None)


class TenantAwareWorker(Worker):
    """
    Context-sensitive background processor.

    An RQ-based worker that automatically restores multi-tenant
    context (tenant_id) before executing background jobs. Ensures
    data isolation and correct configuration loading for asynchronous
    tasks.
    """

    def perform_job(self, job: Any, queue: Any) -> bool:
        """Wraps job execution with the context it was enqueued under.

        Three things happen around ``super().perform_job``:

        1. **Identity** — tenant, user, plugin and the plugin's pinned LLM
           policy are restored from the job's metadata; see :func:`_tenant_for`
           for what a job that names no tenant resolves to.
        2. **Trace** — a ``CONSUMER`` span parented on the ``traceparent`` the
           enqueuer left in the metadata, so the job appears inside the trace
           that asked for it instead of starting an orphan.
        3. **Teardown** — every context token is released in ``finally``, in
           reverse order, even when the job raises.
        """
        from core.context import reset_plugin_context, set_plugin_context
        from core.services.llm.policy import (
            bind_llm_policy,
            policy_from_meta,
            reset_llm_policy,
        )

        meta = job.meta or {}
        tenant_id, system_scope = _tenant_for(meta)
        user_id = meta.get("user_id")
        plugin = meta.get("plugin")
        policy = policy_from_meta(meta.get("llm_policy"))

        token = set_tenant_context(tenant_id) if tenant_id else None
        user_token = set_user_context(user_id) if user_id else None
        plugin_token = set_plugin_context(plugin) if plugin else None
        # Bound, not resolved: this process has no policy resolver installed.
        policy_token = bind_llm_policy(policy) if policy is not None else None
        queue_name = str(
            getattr(queue, "name", None) or getattr(job, "origin", None) or "default"
        )
        try:
            with system_scope or nullcontext():
                with consumer_span(job, queue_name):
                    # RQ ships no py.typed, so the base method is `Any`.
                    performed: bool = super().perform_job(job, queue)
                    return performed
        finally:
            if token is not None:
                reset_tenant_context(token)
            if user_token is not None:
                reset_user_context(user_token)
            if plugin_token is not None:
                reset_plugin_context(plugin_token)
            if policy_token is not None:
                reset_llm_policy(policy_token)


def build_worker(queue_names: list[str], connection: Redis) -> TenantAwareWorker:
    """Create a tenant-aware worker wired to the dead-letter handler.

    ``RQ_WORKER_NAME`` names the worker in Redis instead of RQ's random hex.
    Without it a worker's registration cannot be tied back to the process that
    owns it, so nothing outside the process can answer "is *my* worker still
    alive?" — which is exactly what a container liveness check has to ask. Set
    it to the pod name and the check becomes an equality test.

    Args:
        queue_names: Queues to listen on, in priority order.
        connection: Redis connection to the task-queue database.

    Returns:
        A worker ready to ``work()``.
    """
    from core.task_queue.dead_letter import dead_letter_handler

    queues = [Queue(name, connection=connection) for name in queue_names]
    return TenantAwareWorker(
        queues,
        connection=connection,
        name=os.getenv("RQ_WORKER_NAME") or None,
        exception_handlers=[dead_letter_handler],
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
