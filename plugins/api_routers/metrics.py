"""
Metrics Router.

Provides Prometheus metrics endpoint for monitoring and observability.
Protected by basic authentication.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest

from core.routers.admin import verify_credentials

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def prometheus_metrics(user: str = Depends(verify_credentials)) -> Response:
    """Export Prometheus metrics registered in the process."""

    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
