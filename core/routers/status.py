"""
Status Router.

Provides health checks and system status endpoints used for monitoring
uptime, synthetic metrics, and service readiness.
"""

from typing import Dict


from fastapi import APIRouter, Depends

from core.services.indexing import get_indexing_service
from core.observability import telemetry
from core.middleware import require_admin
from core.config import get_app_config, get_vectorstore_config

_app_config = get_app_config()
_vs_config = get_vectorstore_config()
COLLECTION = _vs_config.collection_name

router = APIRouter(tags=["status"])


@router.get("/health")
def health_check() -> Dict[str, str]:
    """Simple health check endpoint for monitoring (no auth required)."""
    return {"status": "ok"}


@router.get("/status")
def status(user: str = Depends(require_admin)) -> Dict[str, object]:
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
