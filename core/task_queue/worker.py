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
import signal
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from multiprocessing import Process
from types import FrameType
from typing import Any, Protocol

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


#: Seconds the supervisor waits for children to finish the job in flight
#: after a stop signal: inside the chart's 120s worker grace, with room left
#: for the terminate/kill escalation.
DEFAULT_STOP_TIMEOUT_S = 100.0
_RESTART_BACKOFF_INITIAL_S = 1.0
_RESTART_BACKOFF_MAX_S = 60.0
#: A child that ran at least this long before dying is restarted at once.
_STABLE_RUN_S = 60.0
_TERMINATE_GRACE_S = 5.0


class ChildProcess(Protocol):
    """The slice of :class:`multiprocessing.Process` the supervisor uses."""

    @property
    def pid(self) -> int | None: ...

    @property
    def exitcode(self) -> int | None: ...

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def join(self, timeout: float | None = None) -> None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


@dataclass
class _Slot:
    process: ChildProcess | None = None
    started_at: float = 0.0
    failures: int = 0
    next_start_at: float = 0.0


class WorkerSupervisor:
    """Keep ``count`` worker processes alive and stop them on a signal.

    The orchestrator signals PID 1 only, so the supervisor forwards SIGTERM
    and SIGINT to every child (each runs RQ's own warm/cold shutdown). A
    child that exits while the supervisor is not stopping is restarted, with
    exponential backoff per slot so a crash loop cannot spin. On stop it
    joins the children for at most ``stop_timeout`` seconds, then terminates
    and finally kills what is left, so the process always exits.
    """

    def __init__(
        self,
        spawn: Callable[[], ChildProcess],
        count: int,
        *,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT_S,
        poll_interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._spawn = spawn
        self._slots = [_Slot() for _ in range(max(1, count))]
        self._stop_timeout = stop_timeout
        self._poll_interval = poll_interval
        self._clock = clock
        self._sleep = sleep
        self._stopping = False
        self._stop_signum: int = signal.SIGTERM
        self._pid = os.getpid()

    @property
    def stopping(self) -> bool:
        """Whether a stop has been requested."""
        return self._stopping

    def children(self) -> list[ChildProcess]:
        """The child processes currently owned by a slot."""
        return [slot.process for slot in self._slots if slot.process is not None]

    def request_stop(self, signum: int = signal.SIGTERM) -> None:
        """Stop supervising and forward ``signum`` to every live child.

        Safe from a signal handler. A second call forwards again, which RQ
        turns into a cold shutdown (the job in flight is killed).
        """
        if os.getpid() != self._pid:  # inherited copy in a forked child
            return
        self._stopping = True
        self._stop_signum = signum
        for child in self.children():
            self._signal_child(child, signum)

    @staticmethod
    def _signal_child(child: ChildProcess, signum: int) -> None:
        pid = child.pid
        if pid is None or not child.is_alive():
            return
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            return

    def supervise_once(self) -> None:
        """Reap dead children and start the ones whose backoff has elapsed."""
        now = self._clock()
        for index, slot in enumerate(self._slots):
            child = slot.process
            if child is not None and not child.is_alive():
                child.join(0)
                lived = now - slot.started_at
                slot.failures = 0 if lived >= _STABLE_RUN_S else slot.failures + 1
                delay = (
                    0.0
                    if slot.failures == 0
                    else min(
                        _RESTART_BACKOFF_INITIAL_S * 2 ** (slot.failures - 1),
                        _RESTART_BACKOFF_MAX_S,
                    )
                )
                slot.process = None
                slot.next_start_at = now + delay
                if not self._stopping:
                    logger.warning(
                        "rq_worker_child_exited slot=%d exitcode=%s restart_in_s=%.1f",
                        index,
                        child.exitcode,
                        delay,
                    )
            if (
                slot.process is None
                and not self._stopping
                and now >= slot.next_start_at
            ):
                process = self._spawn()
                process.start()
                slot.process = process
                slot.started_at = now
                if self._stopping:
                    # A stop landed between start() and the slot assignment,
                    # so request_stop() could not see this child; it is in
                    # its own process group and would never hear of it.
                    self._signal_child(process, self._stop_signum)

    def shutdown(self) -> None:
        """Join the children within ``stop_timeout``; terminate, then kill."""
        deadline = self._clock() + self._stop_timeout
        for child in self.children():
            child.join(max(0.0, deadline - self._clock()))
        for child in self.children():
            if child.is_alive():
                logger.warning("rq_worker_child_terminate pid=%s", child.pid)
                child.terminate()
                child.join(_TERMINATE_GRACE_S)
            if child.is_alive():
                logger.error("rq_worker_child_kill pid=%s", child.pid)
                child.kill()
                child.join(1.0)

    def run(self) -> None:
        """Supervise until a stop signal arrives, then shut the children down."""
        previous: dict[int, Any] = {}

        def _forward(signum: int, _frame: FrameType | None) -> None:
            self.request_stop(signum)

        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous[sig] = signal.signal(sig, _forward)
        except ValueError:  # not the main thread: the caller owns signals
            previous.clear()
        try:
            while not self._stopping:
                self.supervise_once()
                self._sleep(self._poll_interval)
        finally:
            if not self._stopping:  # leaving on an exception, not a signal
                self.request_stop()
            self._stopping = True
            self.shutdown()
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def _connect(redis_url: str) -> Redis:
    """Open the worker's Redis connection with bounded connects.

    ``socket_connect_timeout`` bounds a connect to a Redis that is down or
    unroutable. ``socket_timeout`` is deliberately left for RQ to set: its
    ``Worker`` raises it to ``dequeue_timeout + 10`` so the blocking BLPOP
    dequeue is never cut short, which a shorter enqueue-side deadline would do.
    """
    config = get_task_queue_config()
    connection: Redis = Redis.from_url(
        redis_url,
        socket_connect_timeout=config.socket_connect_timeout,
        health_check_interval=config.health_check_interval,
    )
    return connection


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
    # A forked child inherits the supervisor's forwarding handlers until RQ
    # installs its own inside work(); a stop signal in that window must act
    # on this process, not re-forward to the parent's other children.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, signal.SIG_DFL)
    # Own process group: a terminal Ctrl-C reaches only the supervisor, which
    # forwards it once. Sharing the group, the child would get SIGINT twice
    # (terminal + forward) and RQ would read the second as a cold shutdown.
    if hasattr(os, "setpgrp"):
        os.setpgrp()
    build_worker(queue_names, _connect(redis_url)).work(with_scheduler=with_scheduler)


def start_worker(
    queue_names: list[str] | None = None,
    concurrency: int = 1,
    with_scheduler: bool = True,
    stop_timeout: float = DEFAULT_STOP_TIMEOUT_S,
) -> None:
    """Start ``concurrency`` workers listening on the configured queues.

    With one worker it runs in the calling process. With more, the calling
    process becomes a :class:`WorkerSupervisor` over ``concurrency`` child
    workers: it forwards SIGTERM/SIGINT to them (the orchestrator signals
    PID 1 only), restarts one that dies unexpectedly, and on shutdown waits
    ``stop_timeout`` for their warm shutdown before terminating them. Every
    worker runs the scheduler — RQ guards it with a Redis lock, so only one
    instance polls at a time.

    Args:
        queue_names: Queues to listen on. Defaults to the configured set.
        concurrency: Number of worker processes (minimum 1).
        with_scheduler: Whether workers also run RQ's scheduler.
        stop_timeout: Seconds the supervisor waits for children to finish
            their job in flight after a stop signal. Keep it below the pod's
            ``terminationGracePeriodSeconds``.
    """
    config = get_task_queue_config()
    redis_url = config.get_redis_url()
    names = list(queue_names or config.queues)
    workers = max(1, concurrency)

    logger.info(f"Starting {workers} RQ worker(s) listening on: {names}")
    logger.info(f"Redis URL: {redact_url_credentials(redis_url)}")

    if workers == 1:
        build_worker(names, _connect(redis_url)).work(with_scheduler=with_scheduler)
        return

    def _spawn() -> Process:
        return Process(
            target=run_worker, args=(redis_url, names, with_scheduler), daemon=False
        )

    WorkerSupervisor(_spawn, workers, stop_timeout=stop_timeout).run()


if __name__ == "__main__":
    try:
        start_worker()
    except KeyboardInterrupt:
        print("\nExiting worker...")
        sys.exit(0)
