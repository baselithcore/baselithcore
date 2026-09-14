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

Exposition format: negotiated from the request's ``Accept`` header. This is
load-bearing, not cosmetic — ``core.observability.metric_context`` attaches a
``trace_id`` exemplar to every sampled HTTP and GenAI histogram observation,
and the Prometheus text format has no syntax for exemplars, so serving it
unconditionally recorded that link and then discarded it on the way out. A
scraper that advertises ``application/openmetrics-text`` (Prometheus does so
whenever exemplar storage is enabled) now gets the OpenMetrics encoding, with
the exemplars in it.

Everything else keeps the Prometheus text format, body unchanged. Its declared
version moves from ``1.0.0`` to ``0.0.4``, because that is what the library's
own negotiator labels an un-negotiated response: the serializer escapes metric
names to legacy-safe form by default, so ``0.0.4`` is the honest — and the more
widely parseable — claim of the two. No scraper loses anything by it.
"""

import os

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from prometheus_client import REGISTRY, CollectorRegistry, multiprocess
from prometheus_client.exposition import choose_encoder

from core.config.security import get_security_config

router = APIRouter(tags=["metrics"])

_basic = HTTPBasic(auto_error=False)


def _render_metrics(accept_header: str) -> tuple[bytes, str]:
    """Serialize the active registry in the format *accept_header* asks for.

    Negotiation is delegated to prometheus_client's own ``choose_encoder``
    rather than hand-parsed here: it is the function the library keeps in step
    with the spec (format version, and the escaping parameter that arrived with
    the UTF-8 name support), and it already falls back to the plain text format
    for any header it does not recognise — including an absent or empty one.

    Args:
        accept_header: Raw ``Accept`` header value; ``""`` when unset.

    Returns:
        The serialized exposition and the content type to declare for it.

    Note:
        Under ``PROMETHEUS_MULTIPROC_DIR`` the samples are rebuilt from the
        per-worker mmap files, which carry no exemplars — prometheus_client
        has nowhere to put them. The negotiated encoding is still honoured, so
        the scraper never sees a format it did not ask for; the bodies simply
        have no exemplars to offer in that deployment shape.
    """
    encoder, content_type = choose_encoder(accept_header)
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return encoder(registry), content_type
    return encoder(REGISTRY), content_type


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

    payload, content_type = _render_metrics(request.headers.get("accept", ""))
    return Response(content=payload, media_type=content_type)
