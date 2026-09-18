"""Dedicated thread pool for synchronous human-in-the-loop callbacks.

A HITL callback is the single most open-ended blocking call in the framework:
it waits on a *person*. A CLI prompt, a modal dialog, a queue the UI feeds —
any of them can take minutes, and a callback that hits its ``timeout`` never
returns its thread at all, because ``concurrent.futures`` threads cannot be
cancelled. The call therefore must not run on the interpreter's default
executor, which is only ``cpu_count + 4`` wide and shared with every other
``to_thread``/``run_in_executor`` caller in the framework — SSRF DNS
resolution on the browser route guard, audit-log appends, tokenization. A
handful of pending approvals would fill it and leave those short,
latency-critical tasks queued behind a human.

Same argument, same shape as
:func:`core.utils.concurrency.get_inference_executor`: a small, separate,
lazily built pool, shut down at interpreter exit. Sized by
``ORCHESTRATOR_HITL_CALLBACK_THREADS`` (default 8) — the number of humans a
single process can usefully be blocked on at once.
"""

from __future__ import annotations

import asyncio
import atexit
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Final

from core.observability.logging import get_logger

logger = get_logger(__name__)


#: Fallback pool width when the config layer is unavailable.
DEFAULT_HITL_CALLBACK_THREADS: Final[int] = 8

_hitl_executor: ThreadPoolExecutor | None = None


def _configured_threads() -> int:
    """Pool width from ``ORCHESTRATOR_HITL_CALLBACK_THREADS`` (default 8).

    Read at call time rather than import time so a test or a late config
    reload is honoured.
    """
    try:
        from core.config.orchestration import get_orchestration_config

        return max(1, int(get_orchestration_config().hitl_callback_threads))
    except Exception:  # silent-ok: a missing config layer must not break approvals; the documented default is the safe answer
        return DEFAULT_HITL_CALLBACK_THREADS


def get_hitl_executor() -> ThreadPoolExecutor:
    """The process-wide thread pool for blocking human-interaction callbacks.

    Deliberately **not** the interpreter's default executor (see the module
    docstring). Built on first use and shut down at interpreter exit.

    Returns:
        The shared HITL ``ThreadPoolExecutor``.
    """
    global _hitl_executor
    if _hitl_executor is None:
        _hitl_executor = ThreadPoolExecutor(
            max_workers=_configured_threads(),
            thread_name_prefix="baselith-hitl",
        )
        atexit.register(shutdown_hitl_executor)
    return _hitl_executor


async def run_hitl_callback[T](fn: Callable[[], T]) -> T:
    """Run a blocking HITL callback on the dedicated pool.

    Drop-in replacement for ``asyncio.to_thread`` at human-callback call
    sites; ``fn`` must already be argument-bound.

    Args:
        fn: The zero-argument blocking callable to run off the loop.

    Returns:
        Whatever ``fn`` returns.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_hitl_executor(), fn)


def shutdown_hitl_executor(*, wait: bool = False) -> None:
    """Tear the pool down (idempotent). Registered with ``atexit``.

    Args:
        wait: Block until in-flight callbacks finish. The default does not:
            a callback waiting on a human would hold interpreter exit open
            indefinitely.
    """
    global _hitl_executor
    executor, _hitl_executor = _hitl_executor, None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=not wait)


__all__ = [
    "DEFAULT_HITL_CALLBACK_THREADS",
    "get_hitl_executor",
    "run_hitl_callback",
    "shutdown_hitl_executor",
]
