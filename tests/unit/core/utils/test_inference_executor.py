"""Model inference must not run on the interpreter's default executor.

The default pool is shared with every other ``to_thread`` caller in the
framework — SSRF DNS resolution on the browser route guard, audit-log appends,
tokenization. Those are short and latency-critical; a burst of embedding or
rerank work would fill the pool and leave them queued behind multi-second model
calls. Inference therefore gets its own small, bounded pool.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from core.utils.concurrency import (
    _DEFAULT_INFERENCE_THREADS,
    _inference_thread_count,
    get_inference_executor,
    run_inference,
    shutdown_inference_executor,
)


@pytest.fixture(autouse=True)
def _fresh_pool():
    """Each test gets a pristine pool and leaves no thread behind."""
    shutdown_inference_executor(wait=True)
    yield
    shutdown_inference_executor(wait=True)


@pytest.mark.asyncio
async def test_runs_off_the_event_loop_thread() -> None:
    loop_thread = threading.get_ident()
    ran_on = await run_inference(threading.get_ident)
    assert ran_on != loop_thread


@pytest.mark.asyncio
async def test_does_not_use_the_default_executor() -> None:
    """The whole point: a distinct pool, identifiable by its thread name."""
    default_names: list[str] = []
    await asyncio.to_thread(
        lambda: default_names.append(threading.current_thread().name)
    )

    inference_name = await run_inference(lambda: threading.current_thread().name)

    assert inference_name.startswith("baselith-inference")
    assert inference_name not in default_names


@pytest.mark.asyncio
async def test_passes_args_and_kwargs() -> None:
    def _f(a, b, *, c):
        return (a, b, c)

    assert await run_inference(_f, 1, 2, c=3) == (1, 2, 3)


@pytest.mark.asyncio
async def test_exceptions_propagate() -> None:
    def _boom():
        raise ValueError("inference failed")

    with pytest.raises(ValueError, match="inference failed"):
        await run_inference(_boom)


@pytest.mark.asyncio
async def test_pool_is_bounded() -> None:
    """A burst must not spawn one thread per call — that is what starves the
    rest of the process."""
    executor = get_inference_executor()
    seen: set[int] = set()
    lock = threading.Lock()

    def _record():
        with lock:
            seen.add(threading.get_ident())

    await asyncio.gather(*(run_inference(_record) for _ in range(50)))
    assert len(seen) <= executor._max_workers


def test_executor_is_reused_across_calls() -> None:
    assert get_inference_executor() is get_inference_executor()


def test_shutdown_is_idempotent_and_rebuilds_on_demand() -> None:
    first = get_inference_executor()
    shutdown_inference_executor(wait=True)
    shutdown_inference_executor(wait=True)  # must not raise
    assert get_inference_executor() is not first


def test_thread_count_default_is_small(monkeypatch) -> None:
    """Inference libraries parallelise internally, so more threads buy
    contention rather than throughput past a small number."""
    monkeypatch.delenv("BASELITH_INFERENCE_THREADS", raising=False)
    assert _inference_thread_count() == _DEFAULT_INFERENCE_THREADS
    assert 1 <= _DEFAULT_INFERENCE_THREADS <= 4


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2", 2), ("16", 16), ("0", 1), ("-3", 1)],
)
def test_thread_count_env_override(monkeypatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("BASELITH_INFERENCE_THREADS", raw)
    assert _inference_thread_count() == expected


def test_malformed_thread_count_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("BASELITH_INFERENCE_THREADS", "many")
    assert _inference_thread_count() == _DEFAULT_INFERENCE_THREADS
