"""
Indexing Router.

Manages the document indexing lifecycle: the status of the background indexing
engine, and operator-triggered incremental reindexing or bootstrapping.
Protected according to security configuration.

Both triggers are asynchronous: they start the run in the background and answer
``202 Accepted`` at once, with the poll URL (``GET /index/status``) in the body
(``status_url``) and in the ``Location`` header. An indexing pass over a large
corpus outlives any reasonable request timeout; running it inside the request
held a worker slot for its whole duration and turned a proxy timeout into a
client-side failure for work that was in fact still running.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from core.api.versioning import V1_PREFIX, api_v1_enabled
from core.config import get_app_config
from core.middleware import require_admin_or_job
from core.services.bootstrap import bootstrapper
from plugins.api_routers.schemas import (
    IndexBootstrapAccepted,
    IndexRunAccepted,
    IndexStatus,
    problem_responses,
)

INDEX_BOOTSTRAP_ENABLED = get_app_config().index_bootstrap_enabled

router = APIRouter(tags=["indexing"], dependencies=[Depends(require_admin_or_job)])


def index_status_url(root_path: str = "") -> str:
    """Where an indexing run is polled: the versioned path when ``/v1`` is on.

    Args:
        root_path: The ASGI ``root_path`` the app is mounted under (behind a
            path-prefixing proxy), so the URL resolves from the client.
    """
    return f"{root_path}{V1_PREFIX if api_v1_enabled() else ''}/index/status"


def _accepted(request: Request, response: Response, mode: str) -> dict[str, object]:
    url = index_status_url(str(request.scope.get("root_path") or ""))
    response.headers["Location"] = url
    return {"status": "scheduled", "mode": mode, "status_url": url}


@router.get(
    "/index/status",
    response_model=IndexStatus,
    response_model_exclude_unset=True,
    responses=problem_responses(401, 403),
)
def index_status() -> dict[str, object]:
    """Retrieve the current status of the background indexing engine."""
    status_payload = bootstrapper.status()
    status_payload["bootstrap_enabled"] = INDEX_BOOTSTRAP_ENABLED
    status_payload["state"] = "running" if status_payload.get("running") else "idle"
    return status_payload


@router.post(
    "/index/bootstrap",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IndexBootstrapAccepted,
    response_model_exclude_unset=True,
    responses=problem_responses(401, 403, 409, 422, 503),
)
async def trigger_bootstrap(
    request: Request, response: Response, force_full: bool = False
) -> dict[str, object]:
    """Schedule a bootstrap indexing run; poll ``status_url`` for the outcome."""
    if not INDEX_BOOTSTRAP_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Bootstrapping disabled via configuration.",
        )
    scheduled = await bootstrapper.schedule(force_full=force_full)
    if not scheduled:
        raise HTTPException(
            status_code=409, detail="An indexing process is already running."
        )
    current = bootstrapper.status()
    return {**current, **_accepted(request, response, str(current["mode"]))}


@router.post(
    "/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IndexRunAccepted,
    responses=problem_responses(401, 403, 409),
)
async def reindex(request: Request, response: Response) -> dict[str, object]:
    """Start an incremental indexing of local documents in the background.

    - Markdown files are read from the folder configured in ``DOCUMENTS_PATH`` (.env)
    - Paths and vector store parameters are defined via .env (QDRANT_PATH, COLLECTION_NAME)

    Answers ``202`` immediately; ``GET /index/status`` reports progress and,
    once done, ``last_completed`` and ``last_new_documents``.
    """
    if not await bootstrapper.schedule_manual("incremental"):
        raise HTTPException(
            status_code=409,
            detail="Indexing unavailable: job already running.",
        )
    return _accepted(request, response, "incremental")
