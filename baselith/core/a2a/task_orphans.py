"""Orphaned A2A tasks: fail what a crash left ``submitted`` / ``working``.

A task is persisted when ``message/send`` returns it. A server that hands back
a ``working`` task and finishes the work in the background updates it later —
unless the process dies first. Nothing used to notice: the row stayed
``working`` forever (in Postgres, across every restart) and a peer polling
``tasks/get`` polled forever.

Two complementary mechanisms close that gap:

* **Lazy check** — :meth:`A2AServer._handle_tasks_get` and ``tasks/resubscribe``
  run :func:`reap_if_orphaned` on the task they are about to return: a
  ``submitted``/``working`` task whose last status change is older than the
  server's ``orphan_timeout_seconds`` is marked ``failed`` with an explanatory
  status message and saved, so the poller gets a terminal state.
* **Sweep** — :func:`sweep_orphaned_tasks` fails every such task in a store at
  once, for a deployment that wants stale rows closed whether or not anyone
  polls (call it from a scheduler or a periodic task). It relies on the
  store's optional ``fail_orphaned`` method, implemented by
  :class:`~baselith.core.a2a.server.InMemoryTaskStore` and
  :class:`~baselith.core.a2a.task_store_postgres.PostgresTaskStore`.

``input-required`` and ``auth-required`` tasks are never touched: they wait on
the client or a human, not on a worker that might have died.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from baselith.core.observability.logging import get_logger

from .types import Message, Task, TaskState

if TYPE_CHECKING:
    from .server import TaskStore

logger = get_logger(__name__)

#: Default silence after which a ``submitted``/``working`` task is presumed lost.
DEFAULT_ORPHAN_TIMEOUT_SECONDS = 3600.0

#: Default bound on how many tasks one sweep fails.
DEFAULT_SWEEP_LIMIT = 100

#: States that mean "a worker should be on this". Only these are ever swept.
ORPHANABLE_STATES = frozenset({TaskState.SUBMITTED, TaskState.WORKING})

#: ``Task.metadata`` key recording that the task was failed as an orphan.
ORPHANED_METADATA_KEY = "orphaned"


@runtime_checkable
class OrphanSweepable(Protocol):
    """Optional store capability backing :func:`sweep_orphaned_tasks`."""

    async def fail_orphaned(
        self,
        *,
        max_age_seconds: float,
        now: float | None = None,
        limit: int = DEFAULT_SWEEP_LIMIT,
    ) -> list[str]:
        """Fail orphaned tasks; return their ids."""
        ...


def is_orphaned(
    task: Task, max_age_seconds: float | None, now: float | None = None
) -> bool:
    """Whether ``task`` waits on a worker that has been silent too long.

    Args:
        task: The task.
        max_age_seconds: Silence threshold; ``None`` disables the check.
        now: Clock override (tests).

    Returns:
        True for a ``submitted``/``working`` task whose last status change is
        older than ``max_age_seconds``.
    """
    if max_age_seconds is None or task.status.state not in ORPHANABLE_STATES:
        return False
    current = time.time() if now is None else now
    return current - float(task.status.timestamp) > max_age_seconds


def mark_orphaned(task: Task, silence_seconds: float, max_age_seconds: float) -> None:
    """Move ``task`` to ``failed`` with a message saying why."""
    reason = (
        f"Task orphaned: no progress for {silence_seconds:.0f}s "
        f"(timeout {max_age_seconds:.0f}s); presumed lost to a crash or restart."
    )
    task.update_state(TaskState.FAILED, Message.agent_message(reason))
    metadata: dict[str, Any] = dict(task.metadata or {})
    metadata[ORPHANED_METADATA_KEY] = {
        "silence_seconds": round(silence_seconds, 3),
        "timeout_seconds": max_age_seconds,
    }
    task.metadata = metadata


async def reap_if_orphaned(
    store: TaskStore,
    task: Task,
    max_age_seconds: float | None,
    now: float | None = None,
) -> Task:
    """Fail and save ``task`` when it is orphaned; return it either way.

    Args:
        store: Where the task lives (the failed state is saved back).
        task: The task about to be returned to a client.
        max_age_seconds: Silence threshold; ``None`` disables the check.
        now: Clock override (tests).

    Returns:
        The task, now terminal when it was orphaned.
    """
    current = time.time() if now is None else now
    if not is_orphaned(task, max_age_seconds, current):
        return task
    assert max_age_seconds is not None  # is_orphaned returned True
    silence = current - float(task.status.timestamp)
    mark_orphaned(task, silence, max_age_seconds)
    await store.save(task)
    logger.warning(
        "a2a_task_orphaned task_id=%s silence=%.0fs timeout=%.0fs",
        task.id,
        silence,
        max_age_seconds,
    )
    return task


async def fail_orphaned_tasks(
    store: TaskStore,
    tasks: Iterable[Task],
    max_age_seconds: float,
    now: float | None = None,
    limit: int = DEFAULT_SWEEP_LIMIT,
) -> list[str]:
    """Fail the orphaned members of ``tasks`` (an in-process store's sweep).

    Args:
        store: Where failed tasks are saved back.
        tasks: Candidate tasks (typically every task the store holds).
        max_age_seconds: Silence threshold.
        now: Clock override (tests).
        limit: Maximum tasks failed.

    Returns:
        Ids of the tasks failed.
    """
    current = time.time() if now is None else now
    failed: list[str] = []
    for task in tasks:
        if len(failed) >= limit:
            break
        if is_orphaned(task, max_age_seconds, current):
            await reap_if_orphaned(store, task, max_age_seconds, current)
            failed.append(task.id)
    return failed


async def sweep_orphaned_tasks(
    store: TaskStore,
    *,
    max_age_seconds: float = DEFAULT_ORPHAN_TIMEOUT_SECONDS,
    now: float | None = None,
    limit: int = DEFAULT_SWEEP_LIMIT,
) -> list[str]:
    """Fail every orphaned task in ``store`` (up to ``limit`` per call).

    Args:
        store: The task store. It must implement ``fail_orphaned``
            (:class:`OrphanSweepable`); a store without it is skipped with a
            warning and nothing is failed.
        max_age_seconds: Silence after which a ``submitted``/``working`` task
            is presumed lost.
        now: Clock override (tests).
        limit: Maximum tasks failed by this call; a larger backlog drains
            across calls.

    Returns:
        Ids of the tasks this call failed.
    """
    if not isinstance(store, OrphanSweepable):
        logger.warning("a2a_orphan_sweep_unsupported store=%s", type(store).__name__)
        return []
    failed = await store.fail_orphaned(
        max_age_seconds=max_age_seconds, now=now, limit=limit
    )
    if failed:
        logger.warning("a2a_orphan_sweep failed=%d", len(failed))
    return failed


__all__ = [
    "DEFAULT_ORPHAN_TIMEOUT_SECONDS",
    "DEFAULT_SWEEP_LIMIT",
    "ORPHANABLE_STATES",
    "ORPHANED_METADATA_KEY",
    "OrphanSweepable",
    "fail_orphaned_tasks",
    "is_orphaned",
    "mark_orphaned",
    "reap_if_orphaned",
    "sweep_orphaned_tasks",
]
