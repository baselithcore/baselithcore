"""Durable rows for MCP task handles, so ``tasks/get`` survives a restart.

The live state of a task — its driver coroutine, the event a parked task waits
on — can only exist in the process running it. What a *poll* needs does not:
the status, the result or error, the input a parked task is waiting for, and
when the handle expires. :class:`~baselith.core.mcp.tasks.TaskStore` writes
that through to a :class:`TaskBackend` on every status transition, so a handle
outlives the process that issued it:

* a **finished** task answers ``tasks/get`` with its stored result from any
  process — after a restart, or on another replica;
* a task still ``working`` (or ``input_required``) carries its executor's
  ``owner`` id and a ``lease_expires_at`` the owner's heartbeat renews. A row
  whose lease ran out has no live driver anywhere — its process died — and is
  failed with the orphan message, by a conditional update so two replicas
  reading it at once cannot both write;
* a task whose lease is still live is running on another instance: its state is
  readable, but ``tasks/update`` and ``tasks/cancel`` must reach the owner.

Rows store wall-clock times (``expires_at``, ``lease_expires_at``); the
in-process :class:`~baselith.core.mcp.tasks.Task` keeps its monotonic clocks.

:class:`InMemoryTaskBackend` is the reference implementation (the contract
tests run against it, and several servers in one process can share one); the
Postgres backend is :mod:`baselith.core.mcp.task_store_postgres`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from baselith.core.mcp.tasks import Task

__all__ = [
    "LIVE_STATUSES",
    "ORPHAN_MESSAGE",
    "InMemoryTaskBackend",
    "TaskBackend",
    "TaskRow",
    "orphaned_row",
    "resolve_task_backend",
    "row_from_task",
    "task_from_row",
]

#: Statuses a live driver is responsible for; only these can be orphaned.
LIVE_STATUSES = frozenset({"working", "input_required"})

#: Reason recorded on a task whose driver ended without an outcome.
ORPHAN_MESSAGE = "Task orphaned: its worker ended without a result."


@dataclass(frozen=True)
class TaskRow:
    """One persisted task handle.

    Attributes:
        task_id: The handle.
        status: Task status (``working``, ``completed``, ...).
        data: JSON-safe payload: ``status_message``, ``result``, ``error``,
            ``input_requests``, ``ttl_ms``, ``poll_interval_ms``.
        expires_at: Wall-clock time the handle stops resolving.
        owner: Instance id of the process running the task.
        lease_expires_at: Wall-clock time the owner's lease runs out.
        updated_at: Wall-clock time of the last write.
    """

    task_id: str
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    expires_at: float = 0.0
    owner: str | None = None
    lease_expires_at: float | None = None
    updated_at: float = field(default_factory=time.time)

    def lease_live(self, now: float | None = None) -> bool:
        """Whether an owner still holds the task (its lease has not run out)."""
        clock = time.time() if now is None else now
        return self.lease_expires_at is not None and self.lease_expires_at > clock


@runtime_checkable
class TaskBackend(Protocol):
    """Persistence contract for task handles (see module docstring)."""

    async def save(self, row: TaskRow) -> None:
        """Insert or overwrite the row for ``row.task_id``."""
        ...

    async def load(self, task_id: str, *, now: float | None = None) -> TaskRow | None:
        """The row for ``task_id``, or ``None`` when absent or expired."""
        ...

    async def fail_orphaned(
        self, row: TaskRow, failed: TaskRow, *, now: float | None = None
    ) -> bool:
        """Replace ``row`` by ``failed`` if it is still live with a lapsed lease.

        Returns:
            ``True`` when this call wrote; ``False`` when the row moved on
            (its owner renewed or finished it, or another reader failed it).
        """
        ...

    async def renew(
        self, task_ids: list[str], owner: str, lease_expires_at: float
    ) -> None:
        """Extend the lease of ``owner``'s live tasks among ``task_ids``."""
        ...

    async def prune(self, *, now: float | None = None) -> int:
        """Delete expired rows; return how many went."""
        ...


def row_from_task(
    task: Task, *, owner: str | None, lease_expires_at: float | None
) -> TaskRow:
    """The row persisting ``task``'s current, poll-visible state."""
    return TaskRow(
        task_id=task.task_id,
        status=task.status,
        data={
            "status_message": task.status_message,
            "result": task.result,
            "error": task.error,
            "input_requests": dict(task.input_requests),
            "ttl_ms": task.ttl_ms,
            "poll_interval_ms": task.poll_interval_ms,
        },
        expires_at=task.expires_at,
        owner=owner,
        lease_expires_at=lease_expires_at,
        updated_at=time.time(),
    )


def task_from_row(row: TaskRow) -> Task:
    """A detached :class:`Task` (no driver here) rebuilt from ``row``."""
    from baselith.core.mcp.tasks import Task

    data = row.data
    remaining = max(row.expires_at - time.time(), 0.0)
    ttl_ms = int(data.get("ttl_ms") or 0) or int(remaining * 1000)
    task = Task(
        task_id=row.task_id,
        status=row.status,
        status_message=data.get("status_message"),
        ttl_ms=ttl_ms,
        poll_interval_ms=int(data.get("poll_interval_ms") or 1000),
        result=data.get("result"),
        error=data.get("error"),
        input_requests=dict(data.get("input_requests") or {}),
        expires_at=row.expires_at,
        remote=True,
    )
    # Monotonic creation time that makes ``expired()`` agree with the row.
    task.created_at = time.monotonic() - (ttl_ms / 1000 - remaining)
    return task


def orphaned_row(row: TaskRow) -> TaskRow:
    """``row`` failed with the orphan reason (owner and lease cleared)."""
    data = dict(row.data)
    data["status_message"] = ORPHAN_MESSAGE
    data["error"] = {"code": -32603, "message": ORPHAN_MESSAGE}
    data["input_requests"] = {}
    return replace(
        row,
        status="failed",
        data=data,
        owner=None,
        lease_expires_at=None,
        updated_at=time.time(),
    )


class InMemoryTaskBackend:
    """Process-local :class:`TaskBackend` (reference implementation).

    It survives a :class:`~baselith.core.mcp.tasks.TaskStore`, not the
    process: useful to share handles between servers in one process and as
    the contract the durable backends are tested against.
    """

    def __init__(self) -> None:
        self._rows: dict[str, TaskRow] = {}

    async def save(self, row: TaskRow) -> None:
        self._rows[row.task_id] = row

    async def load(self, task_id: str, *, now: float | None = None) -> TaskRow | None:
        row = self._rows.get(task_id)
        clock = time.time() if now is None else now
        if row is None or row.expires_at <= clock:
            return None
        return row

    async def fail_orphaned(
        self, row: TaskRow, failed: TaskRow, *, now: float | None = None
    ) -> bool:
        current = self._rows.get(row.task_id)
        if (
            current is None
            or current.status not in LIVE_STATUSES
            or current.lease_live(now)
        ):
            return False
        self._rows[row.task_id] = failed
        return True

    async def renew(
        self, task_ids: list[str], owner: str, lease_expires_at: float
    ) -> None:
        for task_id in task_ids:
            current = self._rows.get(task_id)
            if current is not None and current.owner == owner:
                self._rows[task_id] = replace(
                    current, lease_expires_at=lease_expires_at, updated_at=time.time()
                )

    async def prune(self, *, now: float | None = None) -> int:
        clock = time.time() if now is None else now
        expired = [k for k, row in self._rows.items() if row.expires_at <= clock]
        for task_id in expired:
            del self._rows[task_id]
        return len(expired)


def resolve_task_backend(configured: str) -> TaskBackend | None:
    """The backend ``MCP_TASK_STORE_BACKEND`` names, or ``None`` for memory.

    ``auto`` means Postgres when Postgres storage is enabled, else memory —
    the same resolution as the checkpoint store. ``memory`` persists nothing:
    handles live and die with the process, as before.

    Args:
        configured: ``auto``, ``memory`` or ``postgres``.

    Returns:
        The durable backend, or ``None``.
    """
    backend = (configured or "auto").strip().lower()
    if backend == "auto":
        try:
            from baselith.core.config.storage import get_storage_config

            backend = "postgres" if get_storage_config().postgres_enabled else "memory"
        except Exception:  # silent-ok: no storage config = no Postgres to use
            backend = "memory"
    if backend == "postgres":
        from baselith.core.mcp.task_store_postgres import PostgresTaskBackend

        return PostgresTaskBackend()
    return None
