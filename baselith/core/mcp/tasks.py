"""The ``io.modelcontextprotocol/tasks`` extension.

A blocking call ties a connection to the work behind it; intermediaries time it
out and a dropped client loses the result. A task replaces the response with a
durable handle: the work runs detached, the client polls ``tasks/get``, and a
reconnecting client resumes with the same id.

Opt-in on both sides. The client declares the extension in its per-request
capabilities and the server advertises it in ``server/discover``; a server
**must not** hand a task to a client that never asked for one, because that
client would treat the handle as the answer.

Live tasks are process-local: the driver coroutine runs where the task was
created. Their poll-visible state is written through to a durable
:class:`~baselith.core.mcp.task_persistence.TaskBackend` when one is configured
(``MCP_TASK_STORE_BACKEND``; ``auto`` = Postgres while ``POSTGRES_ENABLED``), so
``tasks/get`` after a restart, or on another replica, returns the stored status
and result. Without one (``memory``) a restart forgets every handle and
``tasks/get`` answers ``Unknown task`` — a terminal error, so a client does not
poll forever.

A ``working`` task whose driver ended without recording an outcome, or that has
been working longer than ``TaskStore(max_working_seconds=...)``, is failed with
a reason when it is next read (the driver is cancelled in the second case). A
stored task still ``working`` or ``input_required`` whose owner's lease lapsed —
its process died — is failed the same way by whichever process reads it next.
``input_required`` tasks wait on the client and are otherwise never swept.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from baselith.core.mcp.errors import InvalidParams
from baselith.core.mcp.task_persistence import (
    LIVE_STATUSES,
    ORPHAN_MESSAGE,
    TaskBackend,
    orphaned_row,
    row_from_task,
    task_from_row,
)
from baselith.core.observability.logging import get_logger

if TYPE_CHECKING:  # re-exported through the module __getattr__ below
    from baselith.core.mcp.task_handlers import TaskHandlerMixin

logger = get_logger(__name__)

EXTENSION_ID = "io.modelcontextprotocol/tasks"

WORKING = "working"
INPUT_REQUIRED = "input_required"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({COMPLETED, FAILED, CANCELLED})

#: Default bound on how long a task may stay ``working`` (one hour).
DEFAULT_MAX_WORKING_SECONDS = 3600.0

#: Lease an executing process holds on each of its durable tasks; its
#: heartbeat renews it every third of this. A stored live task whose lease ran
#: out is failed as orphaned by the next reader.
DEFAULT_TASK_LEASE_SECONDS = 60.0

#: Seconds between best-effort deletions of expired durable rows.
_PRUNE_INTERVAL_SECONDS = 300.0


@dataclass
class Task:
    """One long-running operation and everything a poll needs to report."""

    task_id: str
    status: str = WORKING
    status_message: str | None = None
    ttl_ms: int = 3_600_000
    poll_interval_ms: int = 1_000
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    input_requests: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)
    # When the task last entered ``working`` (creation, or resumption after
    # input): what the max-working bound is measured from.
    working_since: float = field(default_factory=time.monotonic)
    # Answers handed over by tasks/update, awaited by the running coroutine.
    answers: dict[str, Any] = field(default_factory=dict)
    # Wall-clock expiry, what a durable row stores (``created_at`` is monotonic).
    expires_at: float = 0.0
    # Rebuilt from a durable row: no driver in this process.
    remote: bool = field(default=False, repr=False, compare=False)
    _resumed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _task: asyncio.Task[Any] | None = field(default=None, repr=False, compare=False)

    def expired(self, now: float | None = None) -> bool:
        clock = time.monotonic() if now is None else now
        return (clock - self.created_at) * 1000 > self.ttl_ms

    def to_wire(self) -> dict[str, Any]:
        """The ``Task`` object as the client sees it."""
        payload: dict[str, Any] = {
            "taskId": self.task_id,
            "status": self.status,
            "ttlMs": self.ttl_ms,
            "pollIntervalMs": self.poll_interval_ms,
        }
        if self.status_message is not None:
            payload["statusMessage"] = self.status_message
        if self.status == COMPLETED and self.result is not None:
            payload["result"] = self.result
        if self.status == FAILED and self.error is not None:
            payload["error"] = self.error
        if self.status == INPUT_REQUIRED and self.input_requests:
            payload["inputRequests"] = self.input_requests
        return payload


class TaskStore:
    """Task registry with TTL-based eviction, optionally durable.

    Live tasks are in memory: a driver runs in the process that created it.
    With a ``backend``, every status transition is written through (best
    effort: a failed write is logged, the in-process task carries on), each
    live task holds a lease its driver's heartbeat renews, and :meth:`aget`
    answers for handles this process does not hold — issued before a restart
    or by another replica.

    Args:
        max_working_seconds: Longest a task may stay ``working`` before it is
            failed (and its driver cancelled) when next read; ``None`` lets it
            run until its TTL. Orphans — ``working`` with no live driver — are
            failed regardless.
        backend: Durable persistence, or ``None`` (handles die with the
            process).
        lease_seconds: Lease held on each durable live task.
    """

    def __init__(
        self,
        max_working_seconds: float | None = DEFAULT_MAX_WORKING_SECONDS,
        *,
        backend: TaskBackend | None = None,
        lease_seconds: float = DEFAULT_TASK_LEASE_SECONDS,
    ) -> None:
        self._tasks: dict[str, Task] = {}
        self.max_working_seconds = max_working_seconds
        self.backend = backend
        self.lease_seconds = lease_seconds
        #: This process's identity as the owner of the tasks it drives.
        self.owner = secrets.token_hex(8)
        self._pending_writes: set[asyncio.Task[None]] = set()
        self._write_lock: asyncio.Lock | None = None
        self._last_prune = 0.0

    def create(self, ttl_ms: int, poll_interval_ms: int) -> Task:
        self._prune()
        task = Task(
            task_id=secrets.token_urlsafe(16),
            ttl_ms=ttl_ms,
            poll_interval_ms=poll_interval_ms,
            expires_at=time.time() + ttl_ms / 1000,
        )
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> Task:
        """Return the live task held by this process.

        Raises:
            InvalidParams: The id is unknown or its TTL has lapsed — the same
                answer either way, since an expired handle is not a handle.
        """
        task = self._tasks.get(task_id)
        if task is None or task.expired():
            self._tasks.pop(task_id, None)
            raise InvalidParams(f"Unknown task: {task_id}")
        self._reap(task, time.monotonic())
        return task

    async def aget(self, task_id: str) -> Task:
        """Return the task, from this process or from the durable backend.

        A stored task that is still live but whose owner's lease lapsed is
        failed as orphaned (conditionally: a racing reader or a renewing owner
        wins). The result of a backend read is a detached task
        (``remote=True``): its state is readable, nothing here drives it.

        Raises:
            InvalidParams: Unknown or expired everywhere.
        """
        if task_id in self._tasks or self.backend is None:
            return self.get(task_id)
        row = await self.backend.load(task_id)
        if row is None:
            raise InvalidParams(f"Unknown task: {task_id}")
        if row.status in LIVE_STATUSES and not row.lease_live():
            failed = orphaned_row(row)
            if await self.backend.fail_orphaned(row, failed):
                logger.warning("mcp_task_orphaned", task_id=task_id, durable=True)
                row = failed
            else:
                reread = await self.backend.load(task_id)
                if reread is None:
                    raise InvalidParams(f"Unknown task: {task_id}")
                row = reread
        return task_from_row(row)

    async def persist(self, task: Task) -> None:
        """Write ``task``'s current state to the backend (no-op without one).

        Reads the task when the write runs, under one lock, so the last write
        always carries the newest state whatever order transitions queued in.
        """
        if self.backend is None or task.remote:
            return
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        async with self._write_lock:
            live = task.status in LIVE_STATUSES
            row = row_from_task(
                task,
                owner=self.owner,
                lease_expires_at=time.time() + self.lease_seconds if live else None,
            )
            try:
                await self.backend.save(row)
                await self._prune_backend()
            except Exception as exc:
                logger.warning(
                    "mcp_task_persist_failed", task_id=task.task_id, error=str(exc)
                )

    def persist_soon(self, task: Task) -> None:
        """Schedule :meth:`persist` from synchronous code (needs a running loop)."""
        if self.backend is None or task.remote:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        write = loop.create_task(self.persist(task))
        self._pending_writes.add(write)
        write.add_done_callback(self._pending_writes.discard)

    async def heartbeat(self, task: Task) -> None:
        """Renew ``task``'s lease until it reaches a terminal status."""
        if self.backend is None:
            return
        interval = max(self.lease_seconds / 3, 0.01)
        while task.status not in TERMINAL_STATUSES:
            await asyncio.sleep(interval)
            try:
                await self.backend.renew(
                    [task.task_id], self.owner, time.time() + self.lease_seconds
                )
            except Exception as exc:
                logger.warning(
                    "mcp_task_lease_renew_failed", task_id=task.task_id, error=str(exc)
                )

    async def _prune_backend(self) -> None:
        now = time.time()
        if self.backend is None or now - self._last_prune < _PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = now
        await self.backend.prune(now=now)

    def _prune(self) -> None:
        now = time.monotonic()
        for task_id in [t for t, task in self._tasks.items() if task.expired(now)]:
            del self._tasks[task_id]
        for task in self._tasks.values():
            self._reap(task, now)

    def _reap(self, task: Task, now: float) -> None:
        """Fail a ``working`` task nobody will ever finish."""
        if task.status != WORKING:
            return
        driver = task._task
        if driver is not None and driver.done():
            _reconcile(task, driver)
            if task.status != WORKING:
                self.persist_soon(task)
                return
        if driver is None or driver.done():
            _fail(task, ORPHAN_MESSAGE)
            logger.warning("mcp_task_orphaned", task_id=task.task_id)
            self.persist_soon(task)
            return
        limit = self.max_working_seconds
        if limit is not None and (now - task.working_since) > limit:
            # Status first: the driver's cancellation handler must not then
            # report a client cancel the client never asked for.
            _fail(task, f"Task exceeded the {limit:.0f}s working limit.")
            driver.cancel()
            logger.warning("mcp_task_working_timeout", task_id=task.task_id)
            self.persist_soon(task)

    def cancel_all(self) -> None:
        for task in self._tasks.values():
            if task._task is not None:
                task._task.cancel()


def _fail(task: Task, message: str) -> None:
    task.status = FAILED
    task.status_message = message
    task.error = {"code": -32603, "message": message}


def _reconcile(task: Task, finished: asyncio.Task[Any]) -> None:
    """Settle a task whose driver ended without recording a terminal status."""
    if task.status in TERMINAL_STATUSES:
        return
    if finished.cancelled():
        task.status = CANCELLED
        return
    exc = finished.exception()
    if exc is not None:
        task.status = FAILED
        task.error = {"code": -32603, "message": str(exc)}


def _mark_cancelled(task: Task) -> None:
    """Record a cancel unless the task already reached a terminal state."""
    if task.status not in TERMINAL_STATUSES:
        task.status = CANCELLED


# The JSON-RPC side lives in ``task_handlers`` (module size cap); re-exported
# lazily, because that module imports this one.
def __getattr__(name: str) -> Any:  # pragma: no cover - import shim
    if name == "TaskHandlerMixin":
        from baselith.core.mcp.task_handlers import TaskHandlerMixin

        return TaskHandlerMixin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CANCELLED",
    "COMPLETED",
    "DEFAULT_MAX_WORKING_SECONDS",
    "DEFAULT_TASK_LEASE_SECONDS",
    "EXTENSION_ID",
    "FAILED",
    "INPUT_REQUIRED",
    "TERMINAL_STATUSES",
    "WORKING",
    "Task",
    "TaskHandlerMixin",
    "TaskStore",
]
