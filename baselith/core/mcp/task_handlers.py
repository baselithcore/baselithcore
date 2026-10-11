"""JSON-RPC handlers of the ``io.modelcontextprotocol/tasks`` extension.

Split from :mod:`baselith.core.mcp.tasks` (module size cap), which re-exports
:class:`TaskHandlerMixin`. The task model, the store and its durability live
there and in :mod:`baselith.core.mcp.task_persistence`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from baselith.core.mcp.errors import InvalidParams
from baselith.core.mcp.modern import request_meta
from baselith.core.mcp.mrtr import InputRequired
from baselith.core.mcp.tasks import (
    COMPLETED,
    EXTENSION_ID,
    FAILED,
    INPUT_REQUIRED,
    TERMINAL_STATUSES,
    WORKING,
    Task,
    TaskStore,
    _mark_cancelled,
    _reconcile,
)
from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["TaskHandlerMixin"]


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
        store = self._tasks_store
        task = store.create(ttl_ms, poll_interval_ms)
        task._task = asyncio.create_task(self._drive(task, coro_factory))

        def _settled(finished: asyncio.Task[Any]) -> None:
            # A task cancelled before its coroutine ever ran unwinds without
            # entering the body, so reconcile the status from the outside too
            # — then write the terminal state through.
            _reconcile(task, finished)
            store.persist_soon(task)

        task._task.add_done_callback(_settled)
        # Durable before the handle goes out: a restart right after the
        # response must still find it.
        await store.persist(task)
        # Let the work start before the handle goes out, so a poll or a cancel
        # arriving immediately after finds a task that is genuinely running.
        await asyncio.sleep(0)
        logger.info("mcp_task_created", task_id=task.task_id)
        return {"resultType": "task", **task.to_wire()}

    async def _drive(self, task: Task, coro_factory: Any) -> None:
        """Run the work under a lease heartbeat (durable stores only)."""
        heartbeat = (
            asyncio.create_task(self._tasks_store.heartbeat(task))
            if self._tasks_store.backend is not None
            else None
        )
        try:
            await self._drive_work(task, coro_factory)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()

    async def _drive_work(self, task: Task, coro_factory: Any) -> None:
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
                await self._tasks_store.persist(task)
                try:
                    await task._resumed.wait()
                except asyncio.CancelledError:
                    _mark_cancelled(task)
                    raise
                task.status = WORKING
                task.working_since = time.monotonic()
                task.input_requests = {}
                await self._tasks_store.persist(task)
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

    async def _local_task(self, params: dict[str, Any]) -> Task | None:
        """The task to update or cancel; ``None`` when it already finished.

        Only the process driving a task can resume or cancel it. A stored
        task that finished elsewhere needs nothing (``None``); one still live
        on another instance is refused, since its driver is unreachable here.

        Raises:
            InvalidParams: Unknown task, or one running on another instance.
        """
        task_id = str(params.get("taskId", ""))
        task = await self._tasks_store.aget(task_id)
        if not task.remote:
            return task
        if task.status in TERMINAL_STATUSES:
            return None
        raise InvalidParams(
            f"Task {task_id} is running on another server instance; "
            "tasks/update and tasks/cancel must reach the instance that "
            "created it (poll tasks/get until it finishes)."
        )

    async def _handle_task_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/get: the current state of one task."""
        task = await self._tasks_store.aget(str(params.get("taskId", "")))
        return task.to_wire()

    async def _handle_task_update(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/update: supply the input a parked task is waiting on.

        Responses for unknown or already-satisfied keys are ignored rather than
        rejected, per the extension: the client cannot always know what the
        server still needs.
        """
        task = await self._local_task(params)
        if task is None:
            return {}
        answers = params.get("inputResponses")
        if isinstance(answers, dict):
            task.answers.update(answers)
        task._resumed.set()
        return {}

    async def _handle_task_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/cancel — cooperative, so the ack is not a guarantee."""
        task = await self._local_task(params)
        if task is None:
            return {}
        if task.status not in TERMINAL_STATUSES and task._task is not None:
            task._task.cancel()
        logger.info("mcp_task_cancel_requested", task_id=task.task_id)
        return {}
