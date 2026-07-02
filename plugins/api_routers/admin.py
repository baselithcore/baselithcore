"""
Admin Router.

Provides secure endpoints for administrative tasks, including analytics
dashboards and system monitoring. Protected by HTTP Basic Authentication.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pathlib import Path
import secrets

from core.services.feedback_service import get_feedback_service
from core.middleware import (
    verify_admin_password_async,
    check_admin_lockout,
    record_admin_failure,
    clear_admin_failures,
)
from core.config import get_security_config

router = APIRouter(tags=["admin"])
security = HTTPBasic()

BASE_DIR = Path(__file__).resolve().parent.parent / "static"


def _get_admin_user() -> str:
    """Read the admin username lazily from the active security config."""
    return get_security_config().admin_user


async def verify_credentials(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(security),
):
    """
    Verifica credenziali admin via Basic Auth.
    Username and password are read from the .env file via config.py.
    Enforces account lockout after repeated failures.

    Lockout is keyed on the **client IP**, not the (guessable) admin username,
    so an attacker cannot lock the legitimate admin out by hammering the login.
    """
    client_ip = request.client.host if request.client else "unknown"
    await check_admin_lockout(client_ip)

    correct_username = secrets.compare_digest(credentials.username, _get_admin_user())
    # PBKDF2 verification is CPU-bound (100k+ iterations): the async variant
    # offloads it to a thread so it cannot stall other in-flight requests.
    correct_password = await verify_admin_password_async(credentials.password)

    if not (correct_username and correct_password):
        await record_admin_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenziali non valide",
            headers={"WWW-Authenticate": "Basic"},
        )

    await clear_admin_failures(client_ip)
    return credentials.username


@router.get("/admin")
def admin_page(_user: str = Depends(verify_credentials)):
    """
    Restituisce la pagina Admin (protetta da Basic Auth).
    """
    return FileResponse(BASE_DIR / "admin.html")


@router.get("/admin/data")
async def admin_data(
    _user: str = Depends(verify_credentials),
    days: Optional[int] = Query(
        default=30,
        ge=1,
        le=365,
        description="Time window (in days) to consider for analytics. Use values >0.",
    ),
    recent_limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Number of recent feedbacks to return.",
    ),
    top_limit: int = Query(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of entries for popular queries/documents.",
    ),
):
    """
    Returns analytics/feedback data in JSON format (protected by Basic Auth).
    - Aggregated totals (positive, negative, percentages)
    - Daily time series
    - Recent feedback with metadata and sources
    - Most cited queries and documents in the selected period
    """
    feedback_service = get_feedback_service()
    analytics = await feedback_service.get_analytics(
        days=days,
        recent_limit=recent_limit,
        top_limit=top_limit,
    )
    return JSONResponse(analytics)


# ---------------------------------------------------------------------------
# Dead-letter queue (terminally-failed background jobs)
# ---------------------------------------------------------------------------


def _dlq_summary(record) -> dict:
    """Project a DeadLetterRecord to a JSON-safe summary (omits payload)."""
    return {
        "job_id": record.job_id,
        "func_name": record.func_name,
        "origin_queue": record.origin_queue,
        "error": record.error,
        "failed_at": record.failed_at,
        "tenant_id": record.tenant_id,
        "args_repr": record.args_repr,
        "kwargs_repr": record.kwargs_repr,
    }


@router.get("/admin/dlq")
def dlq_list(
    _user: str = Depends(verify_credentials),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List dead-lettered jobs, most-recently-failed first (Basic Auth)."""
    from core.task_queue.dead_letter import get_dead_letter_queue

    dlq = get_dead_letter_queue()
    records = dlq.list(limit=limit, offset=offset)
    return JSONResponse(
        {"total": dlq.count(), "items": [_dlq_summary(r) for r in records]}
    )


@router.get("/admin/dlq/{job_id}")
def dlq_get(job_id: str, _user: str = Depends(verify_credentials)):
    """Return full detail (including traceback) for one dead-lettered job."""
    from core.task_queue.dead_letter import get_dead_letter_queue

    record = get_dead_letter_queue().get(job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    detail = _dlq_summary(record)
    detail["traceback"] = record.traceback
    return JSONResponse(detail)


@router.post("/admin/dlq/{job_id}/replay")
def dlq_replay(job_id: str, _user: str = Depends(verify_credentials)):
    """Re-enqueue a dead-lettered job onto its original queue (Basic Auth)."""
    from core.task_queue.dead_letter import DeadLetterError, get_dead_letter_queue

    try:
        new_id = get_dead_letter_queue().replay(job_id)
    except DeadLetterError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return JSONResponse({"status": "requeued", "job_id": new_id})


@router.delete("/admin/dlq/{job_id}")
def dlq_purge(job_id: str, _user: str = Depends(verify_credentials)):
    """Drop a single dead-lettered job record (Basic Auth)."""
    from core.task_queue.dead_letter import get_dead_letter_queue

    removed = get_dead_letter_queue().purge(job_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return JSONResponse({"status": "purged", "job_id": job_id})


@router.delete("/admin/dlq")
def dlq_purge_all(_user: str = Depends(verify_credentials)):
    """Clear the entire dead-letter queue (Basic Auth)."""
    from core.task_queue.dead_letter import get_dead_letter_queue

    count = get_dead_letter_queue().purge_all()
    return JSONResponse({"status": "purged_all", "removed": count})
