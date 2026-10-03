"""
Metrics Router.

Prometheus exposition endpoint.

Auth: basic auth by default; set ``METRICS_AUTH_REQUIRED=false`` when the
endpoint is only reachable from the scrape network (NetworkPolicy). Two
credentials are accepted: the scrape-only ``METRICS_USERNAME`` /
``METRICS_PASSWORD`` pair, which no other route consults, and — for existing
scrapers — the admin credential. Give Prometheus the scrape-only one: its
ServiceMonitor Secret is readable by an operator in another namespace, and
the admin credential would open ``/admin`` to whoever reads it.

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

import asyncio
import os
import secrets

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from prometheus_client import REGISTRY, CollectorRegistry, multiprocess
from prometheus_client.exposition import choose_encoder

from core.config.security import get_security_config
from core.middleware._admin_auth import authenticate_admin_basic, client_bucket

router = APIRouter(tags=["metrics"])

_basic = HTTPBasic(auto_error=False)


def _is_scrape_credential(credentials: HTTPBasicCredentials) -> bool:
    """True for the configured scrape-only credential, in constant time."""
    config = get_security_config()
    if config.metrics_password is None:
        return False
    scrape_password = config.metrics_password.get_secret_value()
    if not scrape_password:
        # "" is unset, never a credential: a bare ``METRICS_PASSWORD=`` line
        # must not open the endpoint to ``metrics:`` with an empty password.
        return False
    # Both halves are compared on every call, so the response time does not
    # say which one was wrong. Bytes, because compare_digest raises on
    # non-ASCII str.
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        config.metrics_username.encode("utf-8"),
    )
    password_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"), scrape_password.encode("utf-8")
    )
    return user_ok and password_ok


async def _authenticate(request: Request, credentials: HTTPBasicCredentials) -> None:
    """Admit the scrape-only credential or the admin one, under one lockout.

    The lockout runs *before* the scrape compare: both credentials share the
    per-source failure budget, so a source that has exhausted it cannot keep
    confirming a guess against the scrape password from the 200/429 split.
    A wrong scrape guess falls through to the admin check, which records the
    failure against the same bucket.
    """
    from core.middleware.security import get_security_manager

    client_ip = request.client.host if request.client else "unknown"
    await get_security_manager().check_admin_lockout(client_bucket(client_ip))
    if _is_scrape_credential(credentials):
        return
    if not await authenticate_admin_basic(
        client_ip, credentials.username, credentials.password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


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
        await _authenticate(request, credentials)

    # Serialisation (and, under PROMETHEUS_MULTIPROC_DIR, the per-scrape
    # collection of every worker's mmap files) is CPU- and file-bound work:
    # off the event loop, so a scrape never stalls in-flight requests.
    payload, content_type = await asyncio.to_thread(
        _render_metrics, request.headers.get("accept", "")
    )
    return Response(content=payload, media_type=content_type)
