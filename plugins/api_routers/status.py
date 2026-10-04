"""
Status Router.

Provides health checks and system status endpoints used for monitoring
uptime, synthetic metrics, and service readiness.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Response

from core.config import get_app_config, get_vectorstore_config
from core.lifecycle.drain import is_draining
from core.middleware import require_admin
from core.observability import telemetry
from core.observability.health import CachedHealthCheck
from core.services.indexing import get_indexing_service

logger = logging.getLogger(__name__)

_app_config = get_app_config()
_vs_config = get_vectorstore_config()
COLLECTION = _vs_config.collection_name

router = APIRouter(tags=["status"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Liveness probe — process is up. Cheap, no dependency checks, no auth.

    Use for Kubernetes ``livenessProbe``: it must only fail if the process is
    wedged, never because a downstream dependency is unavailable (that is the
    readiness probe's job). ``async def`` on purpose: a sync handler would
    dispatch every probe through the anyio threadpool to return a literal.
    """
    return {"status": "ok"}


async def _check_database() -> bool:
    """Return ``True`` if a trivial query against Postgres succeeds.

    Unbounded here — a pool checkout against an unreachable server waits the
    full ``DB_POOL_TIMEOUT``; :func:`readiness` applies the probe deadline.
    """
    try:
        from core.db.connection import get_async_connection

        async with get_async_connection() as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception as exc:
        logger.warning("Readiness DB check failed: %s", exc)
        return False


async def _check_vectorstore() -> bool:
    """Return ``True`` if the vector store answers and holds the collection.

    Advisory like Redis: recall degrades to keyword search without it, so it
    must not gate readiness — but an operator watching ``/health/ready``
    should see it down. Both an unreachable store and a missing collection
    report ``False``; the log line says which.
    """
    try:
        from core.services.vectorstore.service import get_vectorstore_service

        service = get_vectorstore_service()
        exists_check = getattr(service.provider, "collection_exists", None)
        if exists_check is None:
            return True  # provider without a cheap probe: report nothing worse
        collection = service.config.collection_name
        if not await exists_check(collection):
            logger.info(
                "Readiness vector store check (advisory): reachable, but "
                "collection %r does not exist",
                collection,
            )
            return False
        return True
    except Exception as exc:
        logger.info("Readiness vector store check (advisory) unreachable: %s", exc)
        return False


async def _check_redis() -> bool:
    """Return ``True`` if Redis responds to PING (advisory, not required)."""
    client = None
    try:
        from core.cache.redis_cache import create_redis_client
        from core.config import get_redis_cache_config

        client = create_redis_client(get_redis_cache_config().url)
        await client.ping()
        return True
    except Exception as exc:
        logger.info("Readiness Redis check (advisory) failed: %s", exc)
        return False
    finally:
        if client is not None:
            try:
                # Prefer aclose() (redis-py >=5); fall back to close() for
                # older type stubs/clients that only expose the latter.
                closer = getattr(client, "aclose", None) or client.close
                await closer()
            except Exception:
                pass


_readiness_checker: CachedHealthCheck | None = None
_readiness_lock = asyncio.Lock()
#: Probes still running past their deadline, by dependency name.
_inflight: dict[str, asyncio.Task[bool]] = {}


def get_readiness_checker() -> CachedHealthCheck:
    """The cache behind ``/health/ready``, sized by ``HEALTH_READY_CACHE_TTL``."""
    global _readiness_checker
    if _readiness_checker is None:
        _readiness_checker = CachedHealthCheck(
            cache_ttl=get_app_config().health_ready_cache_ttl
        )
    return _readiness_checker


def reset_readiness_cache() -> None:
    """Drop the cached outcome and re-read the TTL (tests, config reloads).

    The lock is replaced too: once contended, an ``asyncio.Lock`` is bound to
    the event loop it waited on.
    """
    global _readiness_checker, _readiness_lock
    _readiness_checker = None
    _readiness_lock = asyncio.Lock()
    for task in _inflight.values():
        task.cancel()
    _inflight.clear()


def _forget(name: str, task: asyncio.Task[bool]) -> None:
    if _inflight.get(name) is task:
        del _inflight[name]
    if not task.cancelled():
        task.exception()  # retrieved, so asyncio does not log it as lost


async def _bounded(name: str, probe: Callable[[], Awaitable[bool]]) -> bool:
    """Run ``probe`` under the readiness deadline; a probe still running counts as down.

    The probe is waited on, never cancelled: psycopg answers a cancellation
    with a server-side cancel request that itself waits on the dead server, so
    cancelling would not shorten the wait. A probe that overruns keeps running
    in the background and the next refresh waits on *it* rather than stacking
    a second checkout onto the pool.
    """
    timeout = get_app_config().health_ready_probe_timeout
    task = _inflight.get(name)
    if task is None:
        task = asyncio.ensure_future(probe())
        _inflight[name] = task
        task.add_done_callback(lambda t: _forget(name, t))
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        logger.warning("Readiness %s check still pending after %.1fs", name, timeout)
        return False
    try:
        return task.result()
    except (Exception, asyncio.CancelledError):
        return False


@router.get(
    "/health/ready",
    responses={
        503: {
            "description": (
                "Not ready: the process is draining after a stop signal, or "
                "the database is unreachable; the pod should be removed from "
                "Service endpoints."
            )
        }
    },
)
async def readiness(response: Response) -> dict[str, object]:
    """Readiness probe — checks critical dependencies (no auth).

    Returns HTTP 503 when the database is unreachable so Kubernetes removes the
    pod from Service endpoints (traffic draining) until it recovers. Redis and
    the vector store are reported but advisory: the framework degrades without
    them, so they do not gate readiness.

    Each check is bounded by ``HEALTH_READY_PROBE_TIMEOUT``, so a hung
    dependency yields a prompt 503 rather than a probe the kubelet times out.
    The outcome — failure included — is cached for ``HEALTH_READY_CACHE_TTL``
    seconds, and concurrent callers during a refresh wait for the one check in
    flight instead of each starting their own.

    Once the process has received SIGTERM/SIGINT (:func:`is_draining`) it
    answers 503 at once, without touching the cache or the dependencies: a
    draining pod must leave the endpoints even while its database is fine,
    or the proxy keeps routing new requests to a server that is closing.
    """
    if is_draining():
        response.status_code = 503
        return {"status": "draining", "services": {}, "cached": False}

    async def _check() -> dict[str, bool]:
        # Independent probes, concurrently: a cache miss costs one deadline,
        # not the sum of the three.
        database, redis, vectorstore = await asyncio.gather(
            _bounded("database", _check_database),
            _bounded("redis", _check_redis),
            _bounded("vectorstore", _check_vectorstore),
        )
        return {"database": database, "redis": redis, "vectorstore": vectorstore}

    async with _readiness_lock:
        health = await get_readiness_checker().get_status(_check)
    db_ok = health.services.get("database", False)
    response.status_code = 200 if db_ok else 503
    return {
        "status": "ready" if db_ok else "not_ready",
        "services": health.services,
        "cached": health.cached,
    }


@router.get("/status")
def status(user: str = Depends(require_admin)) -> dict[str, object]:
    """
    Returns the system status:
    - Number of indexed documents
    - Qdrant collection in use (from .env)
    - Synthetic metrics (no paths/files)
    """

    metrics = telemetry.snapshot()
    counters = metrics.get("counters", {})
    clarification_summary = {
        "triggered": counters.get("clarification.triggered", 0),
        "no_hits": counters.get("clarification.no_hits", 0),
        "no_reranked_hits": counters.get("clarification.no_reranked_hits", 0),
        "empty_context": counters.get("clarification.empty_context", 0),
    }
    metrics["clarification"] = clarification_summary
    metrics["answers"] = {
        "generated": counters.get("answers.generated", 0),
        "cached": counters.get("answers.cached", 0),
        "clarification": counters.get("answers.clarification", 0),
        "guardrail_block": counters.get("answers.guardrail_block", 0),
        "guardrail_fallback": counters.get("answers.guardrail_fallback", 0),
        "error": counters.get("answers.error", 0),
    }
    metrics["sources"] = {
        "low_coverage": counters.get("sources.low_coverage", 0),
    }

    return {
        "status": "ok",
        "collection": COLLECTION,
        "total_indexed_documents": get_indexing_service().indexed_count,
        "metrics": metrics,
    }
