"""
Status Router.

Provides health checks and system status endpoints used for monitoring
uptime, synthetic metrics, and service readiness.
"""

import logging

from fastapi import APIRouter, Depends, Response

from core.config import get_app_config, get_vectorstore_config
from core.middleware import require_admin
from core.observability import telemetry
from core.observability.health import get_health_checker
from core.services.indexing import get_indexing_service

logger = logging.getLogger(__name__)

_app_config = get_app_config()
_vs_config = get_vectorstore_config()
COLLECTION = _vs_config.collection_name

router = APIRouter(tags=["status"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Liveness probe — process is up. Cheap, no dependency checks, no auth.

    Use for Kubernetes ``livenessProbe``: it must only fail if the process is
    wedged, never because a downstream dependency is unavailable (that is the
    readiness probe's job).
    """
    return {"status": "ok"}


async def _check_database() -> bool:
    """Return ``True`` if a trivial query against Postgres succeeds."""
    try:
        from core.db.connection import get_async_connection

        async with get_async_connection() as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception as exc:
        logger.warning("Readiness DB check failed: %s", exc)
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


@router.get("/health/ready")
async def readiness(response: Response) -> dict[str, object]:
    """Readiness probe — checks critical dependencies (no auth).

    Returns HTTP 503 when the database is unreachable so Kubernetes removes the
    pod from Service endpoints (traffic draining) until it recovers. Redis is
    reported but advisory: the framework degrades to in-memory fallbacks, so it
    does not gate readiness. Results are cached (~30s) to bound probe overhead.
    """
    checker = get_health_checker()

    async def _check() -> dict[str, bool]:
        return {"database": await _check_database(), "redis": await _check_redis()}

    health = await checker.get_status(_check)
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
