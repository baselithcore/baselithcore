"""How many server processes are serving this deployment.

A uvicorn worker is a separate OS process with its own memory. Anything a
plugin keeps in a module-level dict therefore exists *per worker*: a request
that writes it and a later request that reads it are load-balanced
independently, so the read lands on a different process and finds nothing.

The symptom is a resource that exists and then does not — a run listed on one
request and reported missing on the next — which reads as data loss rather
than as a deployment setting. No process can see its own worker count
(uvicorn's parent spawns children that know nothing about each other), so the
launcher records it in the environment and children inherit it.

Plugins holding request-spanning state should check this before choosing an
in-memory backend::

    if get_web_concurrency() > 1 and backend == "memory":
        ...  # refuse, warn, or pick the durable backend instead
"""

from __future__ import annotations

import os

__all__ = [
    "THREAD_POOL_ENV_VARS",
    "WEB_CONCURRENCY_ENV",
    "get_web_concurrency",
    "set_web_concurrency",
    "share_cpu_threads",
]

WEB_CONCURRENCY_ENV = "BASELITH_WEB_CONCURRENCY"

#: The math libraries' thread-pool sizes, read once when torch, numpy or
#: onnxruntime first load — which is why they must be set before any import.
THREAD_POOL_ENV_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")


def get_web_concurrency() -> int:
    """Return the number of server processes, or 1 when single-process.

    Never raises: a malformed value is treated as unknown, and unknown means
    "assume single-process", which is what a plain ``python backend.py`` is.
    """
    raw = os.environ.get(WEB_CONCURRENCY_ENV, "").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def set_web_concurrency(workers: int) -> None:
    """Record the worker count so spawned children inherit it.

    Called by the launcher *before* the workers exist. A value below 2 clears
    the marker rather than writing "1", so a stale value from an outer
    environment cannot outlive a single-process run.
    """
    if workers > 1:
        os.environ[WEB_CONCURRENCY_ENV] = str(workers)
    else:
        os.environ.pop(WEB_CONCURRENCY_ENV, None)


def _available_cpus() -> int:
    """CPUs this process may run on (honours affinity and cgroup cpusets)."""
    affinity = getattr(os, "sched_getaffinity", None)  # absent on macOS
    if affinity is not None:
        try:
            return max(1, len(affinity(0)))
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def share_cpu_threads() -> int | None:
    """Split the CPUs between the workers' math thread pools.

    Every worker process sizes its torch/OpenMP pool to the whole machine,
    so N workers running a model at once start N x cores threads on cores
    threads' worth of CPU. On an 8-core host with four workers that was 32
    busy threads, a load average of 37 and minutes of a stalled API while
    each worker embedded the same documents.

    Gives each worker ``cpus // workers`` threads (at least one) by setting
    :data:`THREAD_POOL_ENV_VARS`, only where the operator set nothing, so an
    explicit ``OMP_NUM_THREADS`` always wins. Must run before torch or numpy
    is imported. A single-process run is left alone.

    Returns:
        The per-worker thread count applied, or ``None`` when single-process.
    """
    workers = get_web_concurrency()
    try:
        workers = max(workers, int(os.environ.get("WEB_CONCURRENCY", "") or 1))
    except ValueError:
        pass
    if workers < 2:
        return None
    per_worker = max(1, _available_cpus() // workers)
    for name in THREAD_POOL_ENV_VARS:
        os.environ.setdefault(name, str(per_worker))
    return per_worker
