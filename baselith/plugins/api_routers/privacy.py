"""
Privacy / Data-Subject-Request Router.

Admin endpoints for the GDPR data-subject rights the
:mod:`baselith.core.privacy` framework can satisfy across registered
providers: export (Art. 15/20), rectification (Art. 16), erasure (Art. 17),
restriction (Art. 18), objection (Art. 21), consent proof and withdrawal
(Art. 7) and retention sweeps (Art. 5(1)(e)). Every route is gated by the
``privacy:manage`` capability scope on top of ``require_user``. Mounted only
when ``PRIVACY_ENABLED`` is set.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from baselith.core.auth.manager import AuthManager
from baselith.core.auth.types import AuthUser
from baselith.core.middleware import require_user
from baselith.core.observability.logging import get_logger
from baselith.core.privacy.consent import get_consent_service
from baselith.core.privacy.service import get_data_subject_service
from baselith.core.privacy.types import SubjectExport
from baselith.plugins.api_routers.schemas import (
    ErasureResult,
    PrivacyProviders,
    RetentionResult,
    problem_responses,
)

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


class RectifyRequest(BaseModel):
    """Art. 16 corrections to apply across providers."""

    subject_id: str = Field(..., min_length=1)
    corrections: dict[str, Any] = Field(..., min_length=1)


class RestrictRequest(BaseModel):
    """Art. 18 restriction (or release) of processing for a subject."""

    subject_id: str = Field(..., min_length=1)
    restricted: bool = True


class ObjectionRequest(BaseModel):
    """An Art. 21 objection to processing."""

    subject_id: str = Field(..., min_length=1)
    processing: str = Field(default="", max_length=500)
    direct_marketing: bool = False
    override_grounds: str | None = Field(default=None, max_length=2000)


class ConsentRequest(BaseModel):
    """An Art. 7 consent decision for one subject and one purpose."""

    subject_id: str = Field(..., min_length=1)
    purpose: str = Field(..., min_length=1, max_length=200)
    notice_version: str = Field(default="", max_length=100)
    evidence: str = Field(default="", max_length=2000)


@router.get(
    "/providers",
    response_model=PrivacyProviders,
    responses=problem_responses(401, 403),
)
async def list_providers(request: Request) -> dict[str, Any]:
    """List the registered data providers (requires ``privacy:manage``)."""
    _enforce(request)
    service = get_data_subject_service()
    return {"providers": [p.name for p in service.registry.all()]}


@router.post(
    "/export",
    response_model=SubjectExport,
    responses=problem_responses(401, 403, 422),
)
async def export_subject(request: Request, payload: SubjectRequest) -> dict[str, Any]:
    """Export all data held for a subject (right to access / portability)."""
    _enforce(request)
    service = get_data_subject_service()
    bundle = await service.export_subject(payload.subject_id)
    # Provider records are arbitrary: encode them as the route always did
    # (see the schemas module docstring), not with pydantic's Any rules.
    return jsonable_encoder(bundle.model_dump())


@router.post(
    "/erase",
    response_model=ErasureResult,
    responses=problem_responses(401, 403, 422),
)
async def erase_subject(request: Request, payload: SubjectRequest) -> dict[str, Any]:
    """Erase all data held for a subject (right to erasure)."""
    _enforce(request)
    service = get_data_subject_service()
    report = await service.erase_subject(payload.subject_id)
    return {**report.model_dump(), "total": report.total}


@router.post(
    "/retention/sweep",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RetentionResult,
    responses=problem_responses(401, 403, 422),
)
async def retention_sweep(
    request: Request, payload: RetentionRequest
) -> dict[str, Any]:
    """Purge records older than the horizon across retention-aware providers."""
    _enforce(request)
    service = get_data_subject_service()
    report = await service.purge_expired(payload.older_than_days * 86400)
    return {**report.model_dump(), "total": report.total}


@router.post("/rectify", responses=problem_responses(401, 403, 422))
async def rectify_subject(request: Request, payload: RectifyRequest) -> dict[str, Any]:
    """Correct inaccurate personal data across providers (right to rectification).

    Providers that hold data but cannot rectify it are reported in
    ``unsupported`` rather than skipped: Art. 19 obliges the controller to
    communicate the rectification to each recipient.
    """
    _enforce(request)
    report = await get_data_subject_service().rectify_subject(
        payload.subject_id, payload.corrections
    )
    return {
        "subject_id": report.subject_id,
        "rectified": report.rectified,
        "unsupported": report.unsupported,
        "total": report.total,
    }


@router.post("/restrict", responses=problem_responses(401, 403, 422))
async def restrict_subject(
    request: Request, payload: RestrictRequest
) -> dict[str, Any]:
    """Restrict or release processing for a subject (right to restriction).

    Restriction is not erasure: the data stays, but a provider honouring the
    flag stops processing it beyond storage.
    """
    _enforce(request)
    report = await get_data_subject_service().restrict_subject(
        payload.subject_id, restricted=payload.restricted
    )
    return {
        "subject_id": report.subject_id,
        "restricted": report.restricted,
        "affected": report.affected,
        "unsupported": report.unsupported,
        "total": report.total,
    }


@router.post("/object", responses=problem_responses(401, 403, 422))
async def record_objection(
    request: Request, payload: ObjectionRequest
) -> dict[str, Any]:
    """Record an Art. 21 objection and apply its outcome.

    An objection to direct marketing (Art. 21(2)/(3)) is absolute: always
    upheld, and any override grounds supplied with it are ignored.
    """
    _enforce(request)
    record = await get_data_subject_service().record_objection(
        payload.subject_id,
        processing=payload.processing,
        direct_marketing=payload.direct_marketing,
        override_grounds=payload.override_grounds,
    )
    result: dict[str, Any] = record.model_dump(mode="json")
    return result


@router.get("/consent/{subject_id}", responses=problem_responses(401, 403))
async def subject_consent(request: Request, subject_id: str) -> dict[str, Any]:
    """Return the subject's active purposes and the Art. 7(1) proof chain."""
    _enforce(request)
    service = get_consent_service()
    return {
        "subject_id": subject_id,
        "active_purposes": await service.active_purposes(subject_id),
        "history": [r.to_dict() for r in await service.history(subject_id)],
    }


@router.post(
    "/consent/grant",
    status_code=status.HTTP_201_CREATED,
    responses=problem_responses(401, 403, 422),
)
async def grant_consent(request: Request, payload: ConsentRequest) -> dict[str, Any]:
    """Record that a subject consented to a purpose."""
    _enforce(request)
    record = await get_consent_service().grant(
        payload.subject_id,
        payload.purpose,
        notice_version=payload.notice_version,
        evidence=payload.evidence,
    )
    return record.to_dict()


@router.post("/consent/withdraw", responses=problem_responses(401, 403, 404, 422))
async def withdraw_consent(request: Request, payload: ConsentRequest) -> dict[str, Any]:
    """Withdraw consent for a purpose (Art. 7(3)).

    Appends a withdrawal record instead of deleting the grant: withdrawal
    operates for the future and must not call into question processing that
    was already lawful. ``404`` when there was no active consent to withdraw.
    """
    _enforce(request)
    record = await get_consent_service().withdraw(payload.subject_id, payload.purpose)
    if record is None:
        raise HTTPException(status_code=404, detail="no active consent to withdraw")
    return record.to_dict()
