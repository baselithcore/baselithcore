"""Per-plugin task nursery.

A plugin that starts background work — a cron loop, a queue consumer, a
websocket pump — had nowhere to register it. The task held a reference to the
plugin instance and its module, so unregistering or hot-reloading the plugin
left the old generation running against the old code while the new one started
alongside it: two live copies, duplicated side effects, and a leak that only
grew with each reload.

The nursery gives each plugin a named group. ``spawn_task`` tracks the task and
reaps it when it finishes; ``cancel_all`` cancels every outstanding task and
*awaits* it, so teardown is complete before the plugin is dropped rather than
merely requested.

Two properties matter more than completeness of teardown:

* **It is bounded.** The await is a :func:`asyncio.wait` with a timeout, because
  a task may catch ``CancelledError`` and refuse to die — and the registry holds
  its lock across ``unregister``. A stuck plugin must cost a logged straggler,
  never a frozen registry. Override with
  ``BASELITH_PLUGIN_TASK_CANCEL_TIMEOUT`` (seconds; 0 = do not wait at all).
* **It closes.** Once ``cancel_all`` starts for a plugin, ``spawn`` refuses new
  work for that plugin until it finishes, so a task spawned *during* teardown
  cannot slip past the cancellation and outlive the generation being torn down.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
from collections.abc import Coroutine, Iterator
from contextlib import AbstractContextManager
from typing import Any

from core.observability.logging import get_logger
from core.utils.logsafe import sanitize_log_value

logger = get_logger(__name__)

#: Seconds to wait for cancelled tasks to unwind before giving up on them.
DEFAULT_CANCEL_TIMEOUT_SECONDS = 10.0

#: Operator override for :data:`DEFAULT_CANCEL_TIMEOUT_SECONDS`.
_CANCEL_TIMEOUT_ENV = "BASELITH_PLUGIN_TASK_CANCEL_TIMEOUT"


def get_cancel_timeout_seconds() -> float:
    """How long teardown waits for a plugin's cancelled tasks to unwind.

    Returns:
        The value of ``BASELITH_PLUGIN_TASK_CANCEL_TIMEOUT`` when it parses as a
        non-negative float, else :data:`DEFAULT_CANCEL_TIMEOUT_SECONDS`.
    """
    raw = os.environ.get(_CANCEL_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_CANCEL_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Ignoring non-numeric %s=%r; using %ss",
            _CANCEL_TIMEOUT_ENV,
            raw,
            DEFAULT_CANCEL_TIMEOUT_SECONDS,
        )
        return DEFAULT_CANCEL_TIMEOUT_SECONDS
    return value if value >= 0 else DEFAULT_CANCEL_TIMEOUT_SECONDS


class PluginTaskClosedError(RuntimeError):
    """Raised when a plugin tries to spawn work while its nursery is closing."""


class PluginTaskNursery:
    """Tracks background asyncio tasks, grouped by owning plugin.

    Not thread-safe on its own: the registry holds it under the same ``RLock``
    that guards every other registry mutation, and tasks only ever run on the
    event loop.
    """

    def __init__(self, cancel_timeout: float | None = None) -> None:
        """
        Args:
            cancel_timeout: Seconds to wait for cancelled tasks; ``None`` reads
                :func:`get_cancel_timeout_seconds` at teardown time.
        """
        self._tasks: dict[str, set[asyncio.Task[Any]]] = {}
        #: Plugins whose teardown is in flight; spawning for them is refused.
        #: Reference-counted, because the registry holds a :meth:`closing` block
        #: across the whole unregister while ``cancel_all`` opens its own inside
        #: it — a plain set would let the inner exit re-open the outer window.
        self._closing: dict[str, int] = {}
        #: Monotonic across the process, so a task name is never reused — the
        #: previous scheme numbered by current set size and handed the same name
        #: to two live tasks as soon as one finished.
        self._counter = itertools.count()
        self._cancel_timeout = cancel_timeout

    def spawn(
        self,
        plugin_name: str,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        """Schedule ``coro`` as a task owned by ``plugin_name``.

        Args:
            plugin_name: The owning plugin.
            coro: The coroutine to run.
            name: Optional task name for debugging; defaults to a generated one.

        Returns:
            The created :class:`asyncio.Task`. A strong reference is held until
            the task completes, so it cannot be garbage-collected mid-flight.

        Raises:
            PluginTaskClosedError: The plugin's teardown is already in flight.
                Accepting the task would let it outlive the generation being
                cancelled — exactly the leak the nursery exists to close.
        """
        if self.is_closing(plugin_name):
            coro.close()
            raise PluginTaskClosedError(
                f"plugin '{plugin_name}' is shutting down; refusing new background task"
            )
        task_name = name or f"plugin:{plugin_name}:{next(self._counter)}"
        task = asyncio.create_task(coro, name=task_name)
        owned = self._tasks.setdefault(plugin_name, set())
        owned.add(task)
        task.add_done_callback(lambda done: self._reap(plugin_name, done))
        return task

    def _reap(self, plugin_name: str, task: asyncio.Task[Any]) -> None:
        """Drop a finished task, logging an unexpected failure once."""
        owned = self._tasks.get(plugin_name)
        if owned is not None:
            owned.discard(task)
            if not owned:
                self._tasks.pop(plugin_name, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "Background task for plugin %s failed: %s",
                sanitize_log_value(plugin_name),
                exc,
            )

    def count(self, plugin_name: str) -> int:
        """Number of outstanding tasks owned by a plugin."""
        return len(self._tasks.get(plugin_name, ()))

    def plugins_with_tasks(self) -> list[str]:
        """Names of plugins currently holding at least one task."""
        return sorted(self._tasks)

    async def cancel_all(self, plugin_name: str) -> int:
        """Cancel every task owned by a plugin and wait, with a deadline.

        Awaiting matters: a bare ``cancel()`` only *requests* cancellation, and
        returning before the task has actually unwound lets the old generation
        keep touching shared state while the new one initializes.

        The wait is **bounded**, because the caller (``registry.unregister``)
        holds the registry lock and a task is free to swallow
        ``CancelledError``. On expiry the stragglers are logged and abandoned —
        a stuck plugin costs a warning, never a frozen registry. While this
        runs, :meth:`spawn` refuses new work for the plugin, so a task started
        mid-teardown cannot slip past the cancellation.

        Args:
            plugin_name: The owning plugin.

        Returns:
            How many tasks were cancelled (including any that outlived the
            deadline).
        """
        # Enter the closing window *before* the empty check: a plugin that owns
        # no tasks right now can still spawn one from inside the shutdown that
        # follows this call, and that task would outlive the generation being
        # torn down.
        with self.closing(plugin_name):
            owned = self._tasks.pop(plugin_name, set())
            if not owned:
                return 0

            for task in owned:
                task.cancel()

            timeout = (
                self._cancel_timeout
                if self._cancel_timeout is not None
                else get_cancel_timeout_seconds()
            )
            # timeout=0 means "do not wait at all": asyncio.wait returns
            # immediately with every task still pending.
            done, pending = await asyncio.wait(owned, timeout=timeout)

            if pending:
                logger.warning(
                    "%d background task(s) for plugin %s did not stop within %ss; "
                    "abandoning them (they are still running and may hold "
                    "resources — see %s to change the deadline). Names: %s",
                    len(pending),
                    sanitize_log_value(plugin_name),
                    timeout,
                    _CANCEL_TIMEOUT_ENV,
                    ", ".join(sorted(t.get_name() for t in pending)),
                )

            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None and not isinstance(exc, asyncio.CancelledError):
                    logger.warning(
                        "Task for plugin %s raised during shutdown: %s",
                        sanitize_log_value(plugin_name),
                        exc,
                    )

            return len(owned)

    @contextlib.contextmanager
    def closing(self, plugin_name: str) -> Iterator[None]:
        """Refuse new background work for a plugin for the duration of a block.

        Teardown is wider than ``cancel_all``: the registry cancels, *then*
        calls ``plugin.shutdown()``, and a task spawned from inside that
        shutdown would slip past the cancellation and outlive the generation
        being torn down. Callers wrap the whole sequence in this block.

        Re-entrant (reference counted), so ``cancel_all`` can open its own
        window inside the registry's without the inner exit re-opening spawning.

        Args:
            plugin_name: The plugin being torn down.
        """
        self._closing[plugin_name] = self._closing.get(plugin_name, 0) + 1
        try:
            yield
        finally:
            remaining = self._closing.get(plugin_name, 0) - 1
            if remaining > 0:
                self._closing[plugin_name] = remaining
            else:
                self._closing.pop(plugin_name, None)

    def is_closing(self, plugin_name: str) -> bool:
        """Whether teardown of a plugin's tasks is currently in flight."""
        return plugin_name in self._closing


class TaskNurseryMixin:
    """Registry-facing surface over :class:`PluginTaskNursery`.

    Kept as a mixin so ``registry.py`` stays under the 500-line cap while
    ``registry.spawn_task(...)`` remains the public entry point.
    """

    #: Provided by PluginRegistry.__init__.
    _nursery: PluginTaskNursery

    def spawn_task(
        self,
        plugin_name: str,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        """Run a coroutine as background work owned by a plugin.

        Tasks registered here are cancelled and awaited when the plugin is
        unregistered or reloaded, so a reload cannot leave the previous
        generation running alongside the new one.

        Args:
            plugin_name: The owning plugin's name.
            coro: The coroutine to schedule.
            name: Optional task name for debugging.

        Returns:
            The scheduled task. The registry keeps a strong reference until it
            completes.

        Raises:
            PluginTaskClosedError: The plugin's teardown is in flight, so new
                background work is refused until it completes.
        """
        return self._nursery.spawn(plugin_name, coro, name=name)

    async def cancel_plugin_tasks(self, plugin_name: str) -> int:
        """Cancel and await every background task owned by a plugin.

        Args:
            plugin_name: The owning plugin's name.

        Returns:
            How many tasks were cancelled (0 when the plugin spawned none).
        """
        return await self._nursery.cancel_all(plugin_name)

    def get_plugin_task_count(self, plugin_name: str) -> int:
        """Number of background tasks a plugin currently has outstanding."""
        return self._nursery.count(plugin_name)

    def closing_plugin(self, plugin_name: str) -> AbstractContextManager[None]:
        """Refuse a plugin's new background work for the duration of a block.

        Wrap cancel **and** shutdown in this, so work spawned from inside
        ``Plugin.shutdown`` cannot outlive the generation being torn down.

        Args:
            plugin_name: The plugin being torn down.

        Returns:
            A re-entrant context manager.
        """
        return self._nursery.closing(plugin_name)


__all__ = [
    "DEFAULT_CANCEL_TIMEOUT_SECONDS",
    "PluginTaskClosedError",
    "PluginTaskNursery",
    "TaskNurseryMixin",
    "get_cancel_timeout_seconds",
]
