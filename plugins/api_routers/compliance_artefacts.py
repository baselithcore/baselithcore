"""
Compliance artefact endpoints — Art. 9, Art. 13, GDPR Art. 35/36 and Art. 22.

Split from :mod:`plugins.api_routers.compliance` to keep both modules inside the
500-line cap; the routes mount under the same ``/compliance`` prefix and share
the ``compliance:manage`` scope.

These four artefacts share a property worth exposing over HTTP: each reports
*which statutory element is missing*, so "do we have a DPIA?" gets a truthful
answer instead of a yes/no that hides an empty section.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["compliance"])


def _enforce(request: Request) -> None:
    from plugins.api_routers.compliance import _enforce as enforce_scope

    enforce_scope(request)


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    """A statutory element is missing — the request is well-formed but unlawful."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
    )


class DraftInstructionsRequest(BaseModel):
    """Draft Art. 13 instructions from the records already on file."""

    system_id: str = Field(..., min_length=1)
    risk_file_id: str | None = None
    monitoring_plan_id: str | None = None
    provider_contact: str | None = None


# === Art. 9 risk management =================================================


@router.get("/risk-management")
async def list_risk_files(
    request: Request, system_id: str | None = None, incomplete_only: bool = False
) -> dict[str, Any]:
    """List Art. 9 risk files, with missing elements and open risks named."""
    _enforce(request)
    from core.compliance.artefact_services import get_risk_management_service

    service = get_risk_management_service()
    if incomplete_only:
        files = await service.incomplete()
    elif system_id:
        files = await service.for_system(system_id)
    else:
        files = await service.list_files()
    overdue = {f.id for f in await service.overdue_reviews()}
    return {
        "files": [{**f.to_dict(), "review_overdue": f.id in overdue} for f in files],
        "count": len(files),
    }


@router.get("/risk-management/{file_id}")
async def get_risk_file(request: Request, file_id: str) -> dict[str, Any]:
    """Fetch one Art. 9 risk file, rendered as Annex IV section 5."""
    _enforce(request)
    from core.compliance.artefact_services import get_risk_management_service

    try:
        file = await get_risk_management_service().require(file_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    return {**file.to_dict(), "markdown": file.to_markdown()}


@router.post("/risk-management/{file_id}/review")
async def review_risk_file(request: Request, file_id: str) -> dict[str, Any]:
    """Record an Art. 9(1) systematic review.

    Refused while risks remain open — a review signed over untreated,
    unverified or unaccepted risks records something that did not happen.
    """
    _enforce(request)
    from core.compliance.artefact_services import get_risk_management_service

    try:
        file = await get_risk_management_service().review(file_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return file.to_dict()


# === Art. 13 instructions for use ===========================================


@router.get("/instructions")
async def list_instructions(
    request: Request, system_id: str | None = None, incomplete_only: bool = False
) -> dict[str, Any]:
    """List Art. 13 instructions, with the missing elements named."""
    _enforce(request)
    from core.compliance.artefact_services import get_instructions_service

    service = get_instructions_service()
    if incomplete_only:
        records = await service.incomplete()
    elif system_id:
        records = await service.for_system(system_id)
    else:
        records = await service.list_instructions()
    return {"instructions": [i.to_dict() for i in records], "count": len(records)}


@router.get("/instructions/{instructions_id}")
async def get_instructions(request: Request, instructions_id: str) -> dict[str, Any]:
    """Fetch one set of instructions, rendered for the deployer."""
    _enforce(request)
    from core.compliance.artefact_services import get_instructions_service

    try:
        record = await get_instructions_service().require(instructions_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    return {**record.to_dict(), "markdown": record.to_markdown()}


@router.post("/instructions/draft", status_code=status.HTTP_201_CREATED)
async def draft_instructions_endpoint(
    request: Request, payload: DraftInstructionsRequest
) -> dict[str, Any]:
    """Draft Art. 13 instructions from the registry, risk file and Art. 72 plan."""
    _enforce(request)
    from core.compliance.artefact_services import (
        get_instructions_service,
        get_risk_management_service,
    )
    from core.compliance.instructions import draft_instructions
    from core.compliance.post_market_service import get_post_market_service
    from core.compliance.registry import (
        AiSystemNotFoundError,
        get_ai_system_registry,
    )

    try:
        system = await get_ai_system_registry().require(payload.system_id)
    except AiSystemNotFoundError as exc:
        raise _not_found(exc) from exc

    risk_file = None
    if payload.risk_file_id:
        try:
            risk_file = await get_risk_management_service().require(
                payload.risk_file_id
            )
        except LookupError as exc:
            raise _not_found(exc) from exc

    plan = None
    if payload.monitoring_plan_id:
        try:
            plan = await get_post_market_service().require(payload.monitoring_plan_id)
        except LookupError as exc:
            raise _not_found(exc) from exc

    record = await get_instructions_service().save(
        draft_instructions(
            system,
            risk_file=risk_file,
            monitoring_plan=plan,
            provider_contact=payload.provider_contact,
        )
    )
    return record.to_dict()


@router.post("/instructions/{instructions_id}/issue")
async def issue_instructions(request: Request, instructions_id: str) -> dict[str, Any]:
    """Mark instructions as issued to deployers.

    Refused while Art. 13(3) elements are missing: instructions that omit the
    limitations or the oversight measures leave the deployer unable to perform
    its own Art. 26 duties.
    """
    _enforce(request)
    from core.compliance.artefact_services import get_instructions_service

    try:
        record = await get_instructions_service().issue(instructions_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return record.to_dict()


# === GDPR Art. 35/36 DPIA ===================================================


@router.get("/dpia")
async def list_dpia(
    request: Request, incomplete_only: bool = False, blocked_only: bool = False
) -> dict[str, Any]:
    """List DPIAs. ``blocked_only`` returns those that may not start processing."""
    _enforce(request)
    from core.compliance.artefact_services import get_dpia_service

    service = get_dpia_service()
    if blocked_only:
        assessments = await service.blocked()
    elif incomplete_only:
        assessments = await service.incomplete()
    else:
        assessments = await service.list_assessments()
    return {
        "assessments": [a.to_dict() for a in assessments],
        "count": len(assessments),
    }


@router.post("/dpia/{assessment_id}/complete")
async def complete_dpia(request: Request, assessment_id: str) -> dict[str, Any]:
    """Mark a DPIA complete, refusing while Art. 35(7) elements are missing.

    Completing does not by itself unlock processing: with a high residual risk,
    Art. 36(1) prior consultation is still owed and ``may_start_processing``
    stays false.
    """
    _enforce(request)
    from core.compliance.artefact_services import get_dpia_service

    try:
        assessment = await get_dpia_service().complete(assessment_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return assessment.to_dict()


@router.post("/dpia/{assessment_id}/prior-consultation")
async def record_prior_consultation(
    request: Request, assessment_id: str
) -> dict[str, Any]:
    """Record the Art. 36(1) consultation of the supervisory authority."""
    _enforce(request)
    from core.compliance.artefact_services import get_dpia_service

    try:
        assessment = await get_dpia_service().record_prior_consultation(assessment_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    return assessment.to_dict()


# === GDPR Art. 22 automated decisions =======================================


@router.get("/automated-decisions")
async def list_automated_decisions(
    request: Request, non_compliant_only: bool = False
) -> dict[str, Any]:
    """List Art. 22 decision-making activities and their safeguard posture."""
    _enforce(request)
    from core.privacy.automated_decisions import get_automated_decision_registry

    registry = get_automated_decision_registry()
    activities = (
        registry.non_compliant() if non_compliant_only else registry.all()
    )
    return {
        "activities": [a.to_dict() for a in activities],
        "count": len(activities),
        "in_scope": len(registry.in_scope()),
    }


@router.get("/automated-decisions/{activity_id}/subject-information")
async def subject_information(request: Request, activity_id: str) -> dict[str, Any]:
    """The Art. 15(1)(h) disclosure to hand a data subject on request."""
    _enforce(request)
    from core.privacy.automated_decisions import get_automated_decision_registry

    activity = get_automated_decision_registry().get(activity_id)
    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Automated decision activity not found: {activity_id}",
        )
    return activity.subject_information()
