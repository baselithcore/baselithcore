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

__all__ = ["WEB_CONCURRENCY_ENV", "get_web_concurrency", "set_web_concurrency"]

WEB_CONCURRENCY_ENV = "BASELITH_WEB_CONCURRENCY"


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
