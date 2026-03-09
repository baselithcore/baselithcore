"""
Admin Router.

Provides secure endpoints for administrative tasks, including analytics
dashboards and system monitoring. Protected by HTTP Basic Authentication.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pathlib import Path
import secrets

from core.services.feedback_service import get_feedback_service
from core.middleware import verify_admin_password
from core.config import get_security_config

ADMIN_USER = get_security_config().admin_user

router = APIRouter(tags=["admin"])
security = HTTPBasic()

BASE_DIR = Path(__file__).resolve().parent.parent / "static"


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """
    Verifica credenziali admin via Basic Auth.
    Username and password are read from the .env file via config.py.
    """
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USER)
    correct_password = verify_admin_password(credentials.password)

    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenziali non valide",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@router.get("/admin")
def admin_page(user: str = Depends(verify_credentials)):
    """
    Restituisce la pagina Admin (protetta da Basic Auth).
    """
    return FileResponse(BASE_DIR / "admin.html")


@router.get("/admin/data")
async def admin_data(
    user: str = Depends(verify_credentials),
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
