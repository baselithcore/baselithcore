"""
Privacy / Data-Subject-Request Router.

Admin endpoints for GDPR data-subject rights — export (access/portability),
erasure, and retention sweeps — gated by the ``privacy:manage`` capability scope
on top of ``require_user``. Mounted only when ``PRIVACY_ENABLED`` is set.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from core.auth.manager import AuthManager
from core.auth.types import AuthUser
from core.middleware import require_user
from core.observability.logging import get_logger
from core.privacy.service import get_data_subject_service

logger = get_logger(__name__)

router = APIRouter(
    prefix="/privacy", tags=["privacy"], dependencies=[Depends(require_user)]
)

_SCOPE = "privacy:manage"


def _enforce(request: Request) -> AuthUser:
    user: AuthUser | None = getattr(request.state, "user", None)
    AuthManager.enforce_scopes(user, _SCOPE)
    assert user is not None
    return user


class SubjectRequest(BaseModel):
    """Identify the data subject for an export or erasure."""

    subject_id: str = Field(..., min_length=1)


class RetentionRequest(BaseModel):
    """Retention sweep horizon."""

    older_than_days: int = Field(..., ge=0)


@router.get("/providers")
async def list_providers(request: Request) -> dict[str, Any]:
    """List the registered data providers (requires ``privacy:manage``)."""
    _enforce(request)
    service = get_data_subject_service()
    return {"providers": [p.name for p in service.registry.all()]}


@router.post("/export")
async def export_subject(request: Request, payload: SubjectRequest) -> dict[str, Any]:
    """Export all data held for a subject (right to access / portability)."""
    _enforce(request)
    service = get_data_subject_service()
    bundle = await service.export_subject(payload.subject_id)
    return bundle.model_dump()


@router.post("/erase")
async def erase_subject(request: Request, payload: SubjectRequest) -> dict[str, Any]:
    """Erase all data held for a subject (right to erasure)."""
    _enforce(request)
    service = get_data_subject_service()
    report = await service.erase_subject(payload.subject_id)
    return {**report.model_dump(), "total": report.total}


@router.post("/retention/sweep", status_code=status.HTTP_202_ACCEPTED)
async def retention_sweep(
    request: Request, payload: RetentionRequest
) -> dict[str, Any]:
    """Purge records older than the horizon across retention-aware providers."""
    _enforce(request)
    service = get_data_subject_service()
    report = await service.purge_expired(payload.older_than_days * 86400)
    return {**report.model_dump(), "total": report.total}
