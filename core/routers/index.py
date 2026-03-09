"""
Indexing Router.

Manages the document indexing lifecycle, including checking the status
of indexing jobs and triggering manual incremental reindexing or bootstrapping.
Protected according to security configuration.
"""

from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core.services.bootstrap import bootstrapper
from core.services.indexing import get_indexing_service
from core.config import get_app_config
from core.middleware import require_admin_or_job

INDEX_BOOTSTRAP_ENABLED = get_app_config().index_bootstrap_enabled

router = APIRouter(tags=["indexing"], dependencies=[Depends(require_admin_or_job)])


@router.get("/index/status")
def index_status() -> Dict[str, object]:
    """Retrieve the current status of the background indexing engine."""
    status = bootstrapper.status()
    status["bootstrap_enabled"] = INDEX_BOOTSTRAP_ENABLED
    status["state"] = "running" if status.get("running") else "idle"
    return status


@router.post("/index/bootstrap")
async def trigger_bootstrap(force_full: bool = False) -> Dict[str, object]:
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
    return {"status": "scheduled", **bootstrapper.status()}


@router.post("/reindex")
async def reindex() -> Dict[str, object]:
    """
    Performs an incremental indexing of local documents (synchronous operation).
    - Markdown files are read from the folder configured in ``DOCUMENTS_PATH`` (.env)
    - Paths and vector store parameters are defined via .env (QDRANT_PATH, COLLECTION_NAME)
    """
    if bootstrapper.is_running():
        raise HTTPException(
            status_code=409,
            detail="Indexing unavailable: job already running.",
        )

    indexing_service = get_indexing_service()
    stats = await indexing_service.index_documents(incremental=True)
    bootstrapper.register_manual_completion("incremental")
    return {"status": "ok", "new_files_indexed": stats.new_documents}
