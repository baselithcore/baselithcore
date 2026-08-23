"""Small async concurrency helpers.

``bounded_gather`` runs many coroutines with a hard ceiling on how many are
in-flight at once — the missing guard on ``asyncio.gather`` fan-outs over
user-controlled cardinality (a source emptied of thousands of documents, a
bulk memory rewrite), which would otherwise open thousands of simultaneous
round-trips and exhaust connections/memory.

``run_inference`` offloads CPU-bound model work (embedding, cross-encoder
reranking) to a **dedicated** thread pool instead of the interpreter default,
so a burst of inference cannot starve the unrelated short tasks that also live
on the default executor — SSRF DNS resolution, audit-log appends, tokenization.
"""

from __future__ import annotations

import asyncio
import atexit
import os
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import ParamSpec, TypeVar

_T = TypeVar("_T")
_P = ParamSpec("_P")


async def bounded_gather(
    awaitables: Iterable[Awaitable[_T]],
    *,
    limit: int,
    return_exceptions: bool = False,
) -> list[_T | BaseException]:
    """Like ``asyncio.gather`` but with at most ``limit`` coroutines in flight.

    Results are returned in submission order. With ``return_exceptions=True`` a
    failing item yields its exception in place (mirroring ``asyncio.gather``);
    otherwise the first exception propagates and the rest are cancelled.

    Args:
        awaitables: The coroutines/awaitables to run.
        limit: Maximum number running concurrently (>= 1).
        return_exceptions: Capture per-item exceptions instead of raising.
    """
    semaphore = asyncio.Semaphore(max(1, limit))

    async def _run(item: Awaitable[_T]) -> _T:
        async with semaphore:
            return await item

    return await asyncio.gather(
        *(_run(item) for item in awaitables),
        return_exceptions=return_exceptions,
    )


# Model inference is CPU-bound and the underlying libraries (torch,
# sentence-transformers) already parallelise internally while holding the GIL
# only briefly. Piling on threads therefore buys throughput up to a point and
# then costs it in contention, so the pool stays small by default and is sized
# off the machine rather than fixed. Override with
# BASELITH_INFERENCE_THREADS when the deployment knows better.
_DEFAULT_INFERENCE_THREADS = min(4, max(1, (os.cpu_count() or 1) // 2))

_inference_executor: ThreadPoolExecutor | None = None


def _inference_thread_count() -> int:
    raw = os.environ.get("BASELITH_INFERENCE_THREADS", "").strip()
    if not raw:
        return _DEFAULT_INFERENCE_THREADS
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_INFERENCE_THREADS


def get_inference_executor() -> ThreadPoolExecutor:
    """The process-wide thread pool for CPU-bound model inference.

    Deliberately **not** the interpreter's default executor. That one is shared
    with every other ``to_thread``/``run_in_executor`` caller in the framework —
    SSRF DNS resolution on the browser route guard, audit-log appends,
    tokenization — and it is unbounded-ish (``cpu_count + 4``). A burst of
    embedding or rerank work would fill it and leave those short, latency-
    critical tasks queued behind multi-second model calls.

    Built on first use and shut down at interpreter exit.
    """
    global _inference_executor
    if _inference_executor is None:
        _inference_executor = ThreadPoolExecutor(
            max_workers=_inference_thread_count(),
            thread_name_prefix="baselith-inference",
        )
        atexit.register(shutdown_inference_executor)
    return _inference_executor


async def run_inference(
    fn: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs
) -> _T:
    """Run a blocking inference call on the dedicated pool.

    Drop-in replacement for ``asyncio.to_thread`` / ``run_in_executor(None, …)``
    at model-inference call sites.
    """
    from functools import partial

    loop = asyncio.get_running_loop()
    call: Callable[[], _T] = partial(fn, *args, **kwargs)
    return await loop.run_in_executor(get_inference_executor(), call)


def shutdown_inference_executor(*, wait: bool = False) -> None:
    """Tear the pool down (idempotent). Registered with ``atexit``."""
    global _inference_executor
    executor, _inference_executor = _inference_executor, None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=not wait)


__all__ = [
    "bounded_gather",
    "get_inference_executor",
    "run_inference",
    "shutdown_inference_executor",
]
