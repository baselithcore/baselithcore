"""Server drain signal for long-lived streams.

A graceful shutdown waits for every open connection to finish before the
lifespan teardown runs. A request that ends on its own is fine; a stream that
only ends when the *client* leaves — an SSE feed, a WebSocket subscription —
never finishes, so uvicorn waits out ``--timeout-graceful-shutdown`` and then
cancels it, logging ``Cancel N running task(s), timeout graceful shutdown
exceeded`` on every restart. The lifespan teardown cannot help: it runs only
after the connections are gone.

This module is the moment in between. :func:`install_drain_signal_hook` wraps
the SIGTERM/SIGINT handlers the ASGI server installed, so the first stop
signal marks the process as *draining* before the server starts waiting. A
stream that awaits :func:`wait_for_drain` alongside its own work can then end
cleanly and let the shutdown complete at once.

The hook must run inside the server's signal capture — the app lifespan
startup is the right place (uvicorn installs its handlers before it loads the
app, so patching ``Server.handle_exit`` from app code is too late).
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from collections.abc import Callable
from types import FrameType
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

_HANDLED_SIGNALS = (signal.SIGTERM, signal.SIGINT)
_HOOK_MARKER = "_baselith_drain_hook"

_lock = threading.Lock()
_draining = False
_events: dict[asyncio.AbstractEventLoop, asyncio.Event] = {}


def is_draining() -> bool:
    """Whether this process has received a stop signal."""
    return _draining


def _event_for(loop: asyncio.AbstractEventLoop) -> asyncio.Event:
    with _lock:
        event = _events.get(loop)
        if event is None:
            event = asyncio.Event()
            _events[loop] = event
            if _draining:
                event.set()
        return event


async def wait_for_drain() -> None:
    """Return once the process starts draining (immediately if it already is)."""
    await _event_for(asyncio.get_running_loop()).wait()


def mark_draining() -> None:
    """Flag the process as draining and wake every :func:`wait_for_drain`.

    Safe from a signal handler and from any thread: each loop's event is set
    through ``call_soon_threadsafe``.
    """
    global _draining
    with _lock:
        if _draining:
            return
        _draining = True
        targets = [(loop, ev) for loop, ev in _events.items() if not loop.is_closed()]
    for loop, event in targets:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:  # loop closed between the snapshot and now
            continue


def _wrap(previous: Any) -> Callable[[int, FrameType | None], Any]:
    def _handler(signum: int, frame: FrameType | None) -> Any:
        mark_draining()
        if callable(previous):
            return previous(signum, frame)
        # SIG_DFL / SIG_IGN: restore it and let the signal act as it would have.
        signal.signal(signum, previous)
        if previous == signal.SIG_DFL:
            os.kill(os.getpid(), signum)
        return None

    setattr(_handler, _HOOK_MARKER, True)
    return _handler


def install_drain_signal_hook() -> bool:
    """Chain :func:`mark_draining` in front of the current stop-signal handlers.

    Idempotent, and a no-op off the main thread (``signal.signal`` refuses to
    run there, e.g. under a test client's portal thread).

    Returns:
        True when the hook is in place after the call.
    """
    if threading.current_thread() is not threading.main_thread():
        return False
    for sig in _HANDLED_SIGNALS:
        previous = signal.getsignal(sig)
        if getattr(previous, _HOOK_MARKER, False):
            continue
        signal.signal(sig, _wrap(previous))
    logger.debug("drain signal hook installed")
    return True


def _reset_for_tests() -> None:
    """Forget the draining state (tests only)."""
    global _draining
    with _lock:
        _draining = False
        _events.clear()


__all__ = [
    "install_drain_signal_hook",
    "is_draining",
    "mark_draining",
    "wait_for_drain",
]
