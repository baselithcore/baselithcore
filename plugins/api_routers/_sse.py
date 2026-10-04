"""Shared Server-Sent Events plumbing for the HTTP streaming routes.

``POST /chat/stream`` and ``GET /runs/{run_id}/events`` both hold a response
open while they wait on something slow (a model, a tool, a reviewer). A stream
that says nothing for a minute is dropped by the first proxy or client idle
timeout on the path, and the drop is indistinguishable from a crash. This
module supplies the two pieces both routes need:

* :data:`KEEPALIVE_FRAME` — an SSE comment, which every conformant consumer
  (``EventSource``, both SDKs) ignores but which is traffic on the wire;
* :class:`HeartbeatSource` — waits for the next item of an async iterator in
  slices of the heartbeat interval **without cancelling it**, so a quiet
  period yields :data:`HEARTBEAT` (send a keepalive) instead of either
  blocking or abandoning the in-flight read.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from collections.abc import AsyncIterator
from typing import Any, Final

from core.config import get_app_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

#: An SSE comment frame: ignored by every consumer, but keeps the socket warm.
KEEPALIVE_FRAME: Final = ": keepalive\n\n"

#: Fallback when the app config predates ``SSE_HEARTBEAT_SECONDS`` (legacy
#: test doubles stub the config with a partial namespace).
DEFAULT_HEARTBEAT_SECONDS: Final = 15.0


class _Heartbeat:
    """Sentinel type: the wait timed out with nothing to send."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "HEARTBEAT"


#: Returned by :meth:`HeartbeatSource.next` when the interval elapsed quietly.
HEARTBEAT: Final = _Heartbeat()


def heartbeat_seconds() -> float:
    """The configured silence budget (``SSE_HEARTBEAT_SECONDS``)."""
    value = getattr(get_app_config(), "sse_heartbeat_seconds", None)
    try:
        seconds = float(value) if value is not None else DEFAULT_HEARTBEAT_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_SECONDS
    return seconds if seconds > 0 else DEFAULT_HEARTBEAT_SECONDS


async def _anext(iterator: AsyncIterator[Any]) -> Any:
    return await iterator.__anext__()


class HeartbeatSource:
    """Pull items from ``source``, surfacing quiet periods as :data:`HEARTBEAT`.

    The pending read runs as a task that survives a timed-out wait, so no item
    is lost and the upstream generator is never cancelled mid-step just
    because the stream was quiet. :meth:`aclose` cancels the pending read and
    then closes the source — in that order, since closing an async generator
    while another task is inside it raises.

    Every read (and the final close) runs in **one** context copied at
    construction. A task per read would otherwise start from a fresh copy
    each time, so a context variable the source sets on one step (the
    request's ``LoopBudget``, the plugin attribution) would be gone on the
    next, and resetting its token in the source's ``finally`` would raise.
    """

    def __init__(self, source: AsyncIterator[Any], interval: float) -> None:
        self._iterator = source.__aiter__()
        self._interval = interval
        self._pending: asyncio.Task[Any] | None = None
        self._context = contextvars.copy_context()

    async def next(self, timeout: float | None = None) -> Any:
        """Return the next item, or :data:`HEARTBEAT` after a quiet interval.

        Args:
            timeout: An upper bound tighter than the interval (e.g. a wall-clock
                deadline); the wait never exceeds ``min(interval, timeout)``.

        Raises:
            StopAsyncIteration: The source is exhausted.
            Exception: Whatever the source raised.
        """
        if self._pending is None:
            self._pending = asyncio.get_running_loop().create_task(
                _anext(self._iterator), context=self._context
            )
        wait = self._interval if timeout is None else min(self._interval, timeout)
        done, _ = await asyncio.wait({self._pending}, timeout=max(wait, 0.0))
        if not done:
            return HEARTBEAT
        task, self._pending = self._pending, None
        return task.result()

    async def aclose(self) -> None:
        """Cancel any in-flight read, then close the source (never raises)."""
        pending, self._pending = self._pending, None
        if pending is not None:
            if not pending.done():
                pending.cancel()
                # ``wait`` never raises the task's own outcome, but does let a
                # cancellation of *this* task through.
                await asyncio.wait({pending})
            if not pending.cancelled():
                # Mark the outcome retrieved: no "exception never retrieved".
                with contextlib.suppress(BaseException):
                    pending.exception()
        aclose = getattr(self._iterator, "aclose", None)
        if aclose is not None:
            try:
                # Same context as the reads: the source's ``finally`` resets
                # the tokens it set there.
                await asyncio.get_running_loop().create_task(
                    aclose(), context=self._context
                )
            except Exception:
                logger.debug("sse_source_close_failed", exc_info=True)


__all__ = [
    "DEFAULT_HEARTBEAT_SECONDS",
    "HEARTBEAT",
    "KEEPALIVE_FRAME",
    "HeartbeatSource",
    "heartbeat_seconds",
]
