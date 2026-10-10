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
It carries the caller's ``contextvars`` across the thread hop (tenant, trace,
budget) the way ``asyncio.to_thread`` does; a bare ``run_in_executor`` does not.

``drain_async_iterator`` goes the other way: it lets a **synchronous** caller
consume an async stream. Returning an async generator from a function annotated
``Iterator[str]`` type-checks against ``Any`` and then fails at the call site
with ``'async_generator' object is not iterable`` — a runtime error a long way
from its cause. This is the bridge that makes the sync surface honest.
"""

from __future__ import annotations

import asyncio
import atexit
import contextvars
import os
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Iterable,
    Iterator,
)
from concurrent.futures import ThreadPoolExecutor
from typing import Any


async def bounded_gather[T](
    awaitables: Iterable[Awaitable[T]],
    *,
    limit: int,
    return_exceptions: bool = False,
) -> list[T | BaseException]:
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

    async def _run(item: Awaitable[T]) -> T:
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


async def run_inference[**P, T](
    fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> T:
    """Run a blocking inference call on the dedicated pool.

    Drop-in replacement for ``asyncio.to_thread`` / ``run_in_executor(None, …)``
    at model-inference call sites — including the ``contextvars`` copy that
    ``asyncio.to_thread`` performs and a bare ``run_in_executor`` does not.

    Without that copy the worker thread runs in an *empty* context: every span
    opened inside the offloaded call is orphaned (a new trace root rather than
    a child of the caller's span), and every contextvar-carried value is
    absent. The tenant is one of those values, so tenant-scoped state read
    inside the call resolved to whatever an unbound context means — a shared
    ``default`` bucket, or a ``TenantContextError`` under strict isolation.

    The copy is taken on the calling thread, so the worker observes exactly
    what the caller had bound. It is a *copy*: anything the callable binds
    stays in the worker, so an offloaded call can never rewrite the caller's
    tenant, span or budget.

    That isolation is **shallow**, and the distinction matters for whoever adds
    the next offload site. Rebinding a contextvar in the worker is isolated;
    *mutating* an object it inherited is not. The copy holds the same
    ``LoopBudget``, DI ``Scope``, ``Colony`` and span objects the caller holds,
    so a callable that charges a budget or registers an agent from the worker
    thread mutates shared state off the event loop, with no lock and none of
    the single-threaded ordering the rest of the framework assumes. No current
    call site does — every one is pure CPU over data it was handed, bar
    ``_fuse_recall``, which only reads. Keep it that way: offload computation,
    not bookkeeping.
    """
    from functools import partial

    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    bound: Callable[[], T] = partial(fn, *args, **kwargs)
    call: Callable[[], T] = partial(context.run, bound)
    return await loop.run_in_executor(get_inference_executor(), call)


def shutdown_inference_executor(*, wait: bool = False) -> None:
    """Tear the pool down (idempotent). Registered with ``atexit``."""
    global _inference_executor
    executor, _inference_executor = _inference_executor, None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=not wait)


async def _anext[T](iterator: AsyncIterator[T]) -> T:
    """``__anext__`` as a real coroutine.

    ``AsyncIterator.__anext__`` is typed ``Awaitable[T]``, and
    ``asyncio.Runner.run`` takes a ``Coroutine``. Awaiting it inside this
    wrapper is what turns one into the other.
    """
    return await iterator.__anext__()


def drain_async_iterator[T](
    open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[T]]],
) -> Iterator[T]:
    """Consume an async stream from synchronous code, item by item.

    ``open_stream`` is a **callable** returning the awaitable that produces the
    stream, not the awaitable itself: it is invoked once the loop exists, so a
    coroutine is never created in one context and awaited in another.

    Built on :class:`asyncio.Runner` (3.11+) rather than a hand-rolled
    ``new_event_loop`` / ``set_event_loop`` / ``close`` dance. The Runner owns
    the loop for the whole drain — every ``__anext__`` must run on the SAME
    loop, which is why ``asyncio.run`` per item cannot work — and on exit it
    runs ``shutdown_asyncgens``, so a stream abandoned part-way still gets its
    ``finally`` blocks and releases whatever it held.

    The caller must not already be inside a running loop; check before calling
    and route such callers to the async API instead. Driving a loop from within
    a running one raises, and doing it on a request thread would block the
    event loop for the whole stream even if it did not.

    Args:
        open_stream: Zero-argument callable returning an awaitable that
            resolves to the async iterator to drain.

    Yields:
        Each item the stream produces, in order.
    """
    with asyncio.Runner() as runner:
        iterator: AsyncIterator[T] = runner.run(open_stream()).__aiter__()
        while True:
            try:
                item = runner.run(_anext(iterator))
            except StopAsyncIteration:
                return
            yield item


__all__ = [
    "bounded_gather",
    "drain_async_iterator",
    "get_inference_executor",
    "run_inference",
    "shutdown_inference_executor",
]
