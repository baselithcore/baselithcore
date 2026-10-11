"""The ``io.modelcontextprotocol/tasks`` extension.

A blocking call ties a connection to the work behind it; intermediaries time it
out and a dropped client loses the result. A task replaces the response with a
durable handle: the work runs detached, the client polls ``tasks/get``, and a
reconnecting client resumes with the same id.

Opt-in on both sides. The client declares the extension in its per-request
capabilities and the server advertises it in ``server/discover``; a server
**must not** hand a task to a client that never asked for one, because that
client would treat the handle as the answer.

Handles are process-local (:class:`TaskStore` is in memory): a restart forgets
them and ``tasks/get`` answers ``Unknown task`` — a terminal error, so a client
does not poll forever. Within a live process, a ``working`` task whose driver
ended without recording an outcome, or that has been working longer than
``TaskStore(max_working_seconds=...)``, is failed with a reason when it is next
read (the driver is cancelled in the second case). ``input_required`` tasks
wait on the client and are never swept.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from baselith.core.mcp.errors import InvalidParams
from baselith.core.mcp.modern import request_meta
from baselith.core.mcp.mrtr import InputRequired
from baselith.core.observability.logging import get_logger

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
    """In-memory task registry with TTL-based eviction.

    Process-local, like the tasks themselves: a handle is durable for the
    lifetime of the server process. A deployment that needs handles to survive
    a restart backs this with shared storage.

    Args:
        max_working_seconds: Longest a task may stay ``working`` before it is
            failed (and its driver cancelled) when next read; ``None`` lets it
            run until its TTL. Orphans — ``working`` with no live driver — are
            failed regardless.
    """

    def __init__(
        self, max_working_seconds: float | None = DEFAULT_MAX_WORKING_SECONDS
    ) -> None:
        self._tasks: dict[str, Task] = {}
        self.max_working_seconds = max_working_seconds

    def create(self, ttl_ms: int, poll_interval_ms: int) -> Task:
        self._prune()
        task = Task(
            task_id=secrets.token_urlsafe(16),
            ttl_ms=ttl_ms,
            poll_interval_ms=poll_interval_ms,
        )
        self._tasks[task.task_id] = task
        return task

    def get(self, task_id: str) -> Task:
        """Return the live task.

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
                return
        if driver is None or driver.done():
            _fail(task, "Task orphaned: its worker ended without a result.")
            logger.warning("mcp_task_orphaned", task_id=task.task_id)
            return
        limit = self.max_working_seconds
        if limit is not None and (now - task.working_since) > limit:
            # Status first: the driver's cancellation handler must not then
            # report a client cancel the client never asked for.
            _fail(task, f"Task exceeded the {limit:.0f}s working limit.")
            driver.cancel()
            logger.warning("mcp_task_working_timeout", task_id=task.task_id)

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


class TaskHandlerMixin:
    """Serves ``tasks/get``, ``tasks/update`` and ``tasks/cancel``."""

    _tasks_store: TaskStore

    @staticmethod
    def client_wants_tasks() -> bool:
        """Whether this request's client opted into the tasks extension."""
        meta = request_meta.get()
        return meta is not None and meta.supports_extension(EXTENSION_ID)

    async def _run_as_task(
        self, coro_factory: Any, ttl_ms: int, poll_interval_ms: int
    ) -> dict[str, Any]:
        """Start *coro_factory* detached and return its ``CreateTaskResult``.

        The task is registered before the response is sent, so the handle is
        valid the moment the client receives it.
        """
        task = self._tasks_store.create(ttl_ms, poll_interval_ms)
        task._task = asyncio.create_task(self._drive(task, coro_factory))
        # A task cancelled before its coroutine ever ran unwinds without
        # entering the body, so reconcile the status from the outside too.
        task._task.add_done_callback(lambda t: _reconcile(task, t))
        # Let the work start before the handle goes out, so a poll or a cancel
        # arriving immediately after finds a task that is genuinely running.
        await asyncio.sleep(0)
        logger.info("mcp_task_created", task_id=task.task_id)
        return {"resultType": "task", **task.to_wire()}

    async def _drive(self, task: Task, coro_factory: Any) -> None:
        """Run the work, translating its outcome into the task's state."""
        while True:
            try:
                result = await coro_factory(task.answers)
                if task.status in TERMINAL_STATUSES:
                    return  # failed by the working-time bound meanwhile
                task.result = result
                task.status = COMPLETED
            except InputRequired as exc:
                # Mid-flight input: park the task and wait for tasks/update.
                task.status = INPUT_REQUIRED
                task.input_requests = exc.requests
                task._resumed.clear()
                try:
                    await task._resumed.wait()
                except asyncio.CancelledError:
                    _mark_cancelled(task)
                    raise
                task.status = WORKING
                task.working_since = time.monotonic()
                task.input_requests = {}
                continue
            except asyncio.CancelledError:
                _mark_cancelled(task)
                raise
            except Exception as exc:
                logger.warning("mcp_task_failed", task_id=task.task_id, error=str(exc))
                if task.status not in TERMINAL_STATUSES:
                    task.status = FAILED
                    task.error = {"code": -32603, "message": str(exc)}
            return

    async def _handle_task_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/get: the current state of one task."""
        task = self._tasks_store.get(str(params.get("taskId", "")))
        return task.to_wire()

    async def _handle_task_update(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/update: supply the input a parked task is waiting on.

        Responses for unknown or already-satisfied keys are ignored rather than
        rejected, per the extension: the client cannot always know what the
        server still needs.
        """
        task = self._tasks_store.get(str(params.get("taskId", "")))
        answers = params.get("inputResponses")
        if isinstance(answers, dict):
            task.answers.update(answers)
        task._resumed.set()
        return {}

    async def _handle_task_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/cancel — cooperative, so the ack is not a guarantee."""
        task = self._tasks_store.get(str(params.get("taskId", "")))
        if task.status not in TERMINAL_STATUSES and task._task is not None:
            task._task.cancel()
        logger.info("mcp_task_cancel_requested", task_id=task.task_id)
        return {}


__all__ = [
    "CANCELLED",
    "COMPLETED",
    "DEFAULT_MAX_WORKING_SECONDS",
    "EXTENSION_ID",
    "FAILED",
    "INPUT_REQUIRED",
    "TERMINAL_STATUSES",
    "WORKING",
    "Task",
    "TaskHandlerMixin",
    "TaskStore",
]
