"""
Metrics Router.

Prometheus exposition endpoint.

Auth: admin basic auth by default; set ``METRICS_AUTH_REQUIRED=false`` when
the endpoint is only reachable from the scrape network (NetworkPolicy) or
the scraper is configured with credentials (ServiceMonitor ``basicAuth``).

Multiprocess: when ``PROMETHEUS_MULTIPROC_DIR`` is set (required for
``WEB_CONCURRENCY>1`` — each uvicorn worker otherwise exports only its own
registry), aggregates across worker processes via ``MultiProcessCollector``.
The collector is built per-scrape, as prometheus_client documents, so worker
births/deaths between scrapes are always reflected.
"""

import os

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    generate_latest,
    multiprocess,
)

from core.config.security import get_security_config

router = APIRouter(tags=["metrics"])

_basic = HTTPBasic(auto_error=False)


def _render_metrics() -> bytes:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry)
    return generate_latest(REGISTRY)


@router.get(
    "/metrics",
    # Auth is enforced imperatively below (conditional on
    # METRICS_AUTH_REQUIRED, default ON), so no Depends() populates the spec;
    # declare the requirement explicitly or the contract under-reports it.
    openapi_extra={"security": [{"HTTPBasic": []}]},
)
async def prometheus_metrics(request: Request) -> Response:
    """Export Prometheus metrics (aggregated across workers when configured)."""
    if get_security_config().metrics_auth_required:
        credentials: HTTPBasicCredentials | None = await _basic(request)
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Basic"},
            )
        from plugins.api_routers.admin import verify_credentials

        await verify_credentials(request, credentials)

    return Response(content=_render_metrics(), media_type=CONTENT_TYPE_LATEST)
