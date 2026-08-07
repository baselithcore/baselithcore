"""
AI Governance / Compliance Router.

Admin endpoints over the AI system registry and the governance artefacts that
attach to it — Annex IV technical documentation, Art. 27 FRIA, GDPR Art. 30
ROPA, Art. 72 post-market monitoring — gated by the ``compliance:manage``
capability scope on top of ``require_user``. Mounted only when
``COMPLIANCE_ENABLED`` is set.

These records exist to be *shown* — to a DPO, an internal auditor, a market
surveillance authority. Reaching them only from a Python REPL is not a surface
those readers have, which is why this router exists.

The API is deliberately read-heavy plus the few state transitions that carry a
regulatory meaning (register, reclassify, advance lifecycle, review, observe).
Authoring an Annex IV section or a FRIA is document work that belongs in a
document tool, not in a JSON POST.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.auth.manager import AuthManager
from core.auth.types import AuthUser
from core.middleware import require_user
from core.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/compliance", tags=["compliance"], dependencies=[Depends(require_user)]
)

_SCOPE = "compliance:manage"


def _enforce(request: Request) -> AuthUser:
    user: AuthUser | None = getattr(request.state, "user", None)
    AuthManager.enforce_scopes(user, _SCOPE)
    assert user is not None
    return user


class RegisterSystemRequest(BaseModel):
    """Declare an AI system for the registry."""

    name: str = Field(..., min_length=1)
    version: str = "0.0.0"
    role: str = "provider"
    intended_purpose: str = ""
    description: str = ""
    annex_iii_areas: list[str] = Field(default_factory=list)
    annex_i_product: bool = False
    art6_derogations: list[str] = Field(default_factory=list)
    performs_profiling: bool = False
    interacts_with_humans: bool = False
    generates_synthetic_content: bool = False
    is_gpai_model: bool = False
    gpai_systemic_risk: bool = False
    provider_name: str | None = None
    deployers: list[str] = Field(default_factory=list)
    member_states: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    human_oversight_contacts: list[str] = Field(default_factory=list)
    prohibited_practices: list[str] = Field(default_factory=list)


class LifecycleRequest(BaseModel):
    """Move a registered system to a new lifecycle stage."""

    stage: str = Field(..., min_length=1)


class ObservationRequest(BaseModel):
    """A production measurement against a post-market monitoring plan."""

    metric: str = Field(..., min_length=1)
    value: float
    context: dict[str, Any] = Field(default_factory=dict)


def _bad_request(exc: Exception) -> HTTPException:
    """Map a domain-level value error to a 400 with its message intact."""
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# === Registry ===============================================================


@router.get("/systems")
async def list_systems(
    request: Request, risk_category: str | None = None
) -> dict[str, Any]:
    """List registered AI systems, optionally filtered by risk category."""
    _enforce(request)
    from core.compliance.registry import get_ai_system_registry
    from core.compliance.types import RiskCategory

    category = None
    if risk_category:
        try:
            category = RiskCategory(risk_category)
        except ValueError as exc:
            raise _bad_request(exc) from exc
    systems = await get_ai_system_registry().list_systems(risk_category=category)
    return {"systems": [s.to_dict() for s in systems], "count": len(systems)}


@router.post("/systems", status_code=status.HTTP_201_CREATED)
async def register_system(
    request: Request, payload: RegisterSystemRequest
) -> dict[str, Any]:
    """Register a system: screens Art. 5, derives the Art. 6 risk category.

    A declared prohibited practice yields a ``PROHIBITED`` record rather than a
    rejection — inventorying a banned system is a prerequisite to retiring it.
    Set ``COMPLIANCE_BLOCK_PROHIBITED_PRACTICES`` to refuse instead.
    """
    _enforce(request)
    from core.compliance.prohibited import ProhibitedPractice
    from core.compliance.registry import get_ai_system_registry
    from core.compliance.types import (
        AiSystem,
        AnnexIIIArea,
        Art6Derogation,
        OperatorRole,
    )
    from core.config.compliance import get_compliance_config

    try:
        practices = [ProhibitedPractice(p) for p in payload.prohibited_practices]
        system = AiSystem(
            name=payload.name,
            version=payload.version,
            role=OperatorRole(payload.role),
            intended_purpose=payload.intended_purpose,
            description=payload.description,
            annex_iii_areas=[AnnexIIIArea(a) for a in payload.annex_iii_areas],
            annex_i_product=payload.annex_i_product,
            art6_derogations=[Art6Derogation(d) for d in payload.art6_derogations],
            performs_profiling=payload.performs_profiling,
            interacts_with_humans=payload.interacts_with_humans,
            generates_synthetic_content=payload.generates_synthetic_content,
            is_gpai_model=payload.is_gpai_model,
            gpai_systemic_risk=payload.gpai_systemic_risk,
            provider_name=payload.provider_name,
            deployers=payload.deployers,
            member_states=payload.member_states,
            models=payload.models,
            human_oversight_contacts=payload.human_oversight_contacts,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc

    if practices and get_compliance_config().block_prohibited_practices:
        from core.compliance.prohibited import (
            ProhibitedPracticeError,
            enforce_practices,
        )

        try:
            enforce_practices(payload.name, practices)
        except ProhibitedPracticeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    stored, classification = await get_ai_system_registry().register(
        system, prohibited_practices=practices or None
    )
    return {
        "system": stored.to_dict(),
        "classification": classification.to_dict() if classification else None,
    }


@router.get("/systems/{system_id}")
async def get_system(request: Request, system_id: str) -> dict[str, Any]:
    """Fetch one registered system with the obligations its category carries."""
    _enforce(request)
    from core.compliance.registry import (
        AiSystemNotFoundError,
        get_ai_system_registry,
    )

    registry = get_ai_system_registry()
    try:
        system = await registry.require(system_id)
    except AiSystemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return {
        "system": system.to_dict(),
        "obligations": await registry.obligations(system_id),
    }


@router.post("/systems/{system_id}/reclassify")
async def reclassify_system(request: Request, system_id: str) -> dict[str, Any]:
    """Re-derive the Art. 6 category after the system's facts changed."""
    _enforce(request)
    from core.compliance.registry import (
        AiSystemNotFoundError,
        get_ai_system_registry,
    )

    try:
        system, result = await get_ai_system_registry().reclassify(system_id)
    except AiSystemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return {"system": system.to_dict(), "classification": result.to_dict()}


@router.post("/systems/{system_id}/lifecycle")
async def advance_lifecycle(
    request: Request, system_id: str, payload: LifecycleRequest
) -> dict[str, Any]:
    """Move a system to a new lifecycle stage, stamping the relevant date."""
    _enforce(request)
    from core.compliance.registry import (
        AiSystemNotFoundError,
        get_ai_system_registry,
    )
    from core.compliance.types import LifecycleStage

    try:
        stage = LifecycleStage(payload.stage)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    try:
        system = await get_ai_system_registry().advance_lifecycle(system_id, stage)
    except AiSystemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return system.to_dict()


@router.get("/summary")
async def inventory_summary(request: Request) -> dict[str, Any]:
    """Inventory roll-up: counts by category, and the open Art. 49 duties."""
    _enforce(request)
    from core.compliance.registry import get_ai_system_registry

    return await get_ai_system_registry().summary()


@router.get("/pending-registration")
async def pending_registration(request: Request) -> dict[str, Any]:
    """Systems owing an Art. 49 EU-database registration that has not happened."""
    _enforce(request)
    from core.compliance.registry import get_ai_system_registry

    pending = await get_ai_system_registry().unregistered_with_authority()
    return {"systems": [s.to_dict() for s in pending], "count": len(pending)}


# === Governance artefacts ===================================================


@router.get("/documentation")
async def list_documentation(
    request: Request, system_id: str | None = None, incomplete_only: bool = False
) -> dict[str, Any]:
    """List Annex IV technical documentation, with the missing sections named."""
    _enforce(request)
    from core.compliance.documents import get_technical_documentation_service

    service = get_technical_documentation_service()
    if incomplete_only:
        documents = await service.incomplete()
    elif system_id:
        documents = await service.for_system(system_id)
    else:
        documents = await service.list_documents()
    return {"documents": [d.to_dict() for d in documents], "count": len(documents)}


@router.post("/documentation/draft", status_code=status.HTTP_201_CREATED)
async def draft_documentation(request: Request, system_id: str) -> dict[str, Any]:
    """Draft an Annex IV document from a registered system's known facts."""
    _enforce(request)
    from core.compliance.annex_iv import draft_from_system
    from core.compliance.documents import get_technical_documentation_service
    from core.compliance.registry import (
        AiSystemNotFoundError,
        get_ai_system_registry,
    )

    try:
        system = await get_ai_system_registry().require(system_id)
    except AiSystemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    document = await get_technical_documentation_service().save(
        draft_from_system(system)
    )
    return document.to_dict()


@router.get("/fria")
async def list_fria(
    request: Request, system_id: str | None = None, incomplete_only: bool = False
) -> dict[str, Any]:
    """List Art. 27 FRIAs, with the missing statutory elements named."""
    _enforce(request)
    from core.compliance.documents import get_fria_service

    service = get_fria_service()
    if incomplete_only:
        assessments = await service.incomplete()
    elif system_id:
        assessments = await service.for_system(system_id)
    else:
        assessments = await service.list_assessments()
    return {
        "assessments": [a.to_dict() for a in assessments],
        "count": len(assessments),
    }


@router.get("/ropa")
async def list_ropa(request: Request, incomplete_only: bool = False) -> dict[str, Any]:
    """List the GDPR Art. 30 register, with the missing elements named."""
    _enforce(request)
    from core.compliance.documents import get_ropa_service

    service = get_ropa_service()
    activities = (
        await service.incomplete()
        if incomplete_only
        else await service.list_activities()
    )
    return {
        "activities": [a.to_dict() for a in activities],
        "count": len(activities),
    }


# === Post-market monitoring =================================================


@router.get("/post-market")
async def list_post_market(
    request: Request, system_id: str | None = None
) -> dict[str, Any]:
    """List Art. 72 monitoring plans, flagging overdue reviews."""
    _enforce(request)
    from core.compliance.post_market_service import get_post_market_service

    service = get_post_market_service()
    plans = (
        await service.for_system(system_id) if system_id else await service.list_plans()
    )
    overdue = {p.id for p in await service.overdue_reviews()}
    return {
        "plans": [{**p.to_dict(), "review_overdue": p.id in overdue} for p in plans],
        "count": len(plans),
    }


@router.post("/post-market/{plan_id}/observe")
async def record_observation(
    request: Request, plan_id: str, payload: ObservationRequest
) -> dict[str, Any]:
    """Record a production measurement against a plan and evaluate its threshold."""
    _enforce(request)
    from core.compliance.post_market_service import get_post_market_service

    try:
        observation = await get_post_market_service().observe(
            plan_id, payload.metric, payload.value, context=payload.context
        )
    except KeyError as exc:
        # KeyError subclasses LookupError, so it must be handled first: an
        # undeclared metric is a 400 (a plan gap), not a missing-plan 404.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return observation.to_dict()


@router.post("/post-market/{plan_id}/review")
async def review_plan(request: Request, plan_id: str) -> dict[str, Any]:
    """Record a plan review, resetting the Art. 72(1) cadence."""
    _enforce(request)
    from core.compliance.post_market_service import get_post_market_service

    try:
        plan = await get_post_market_service().review(plan_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return plan.to_dict()


# The Art. 9 / Art. 13 / DPIA / Art. 22 artefact endpoints live in a sibling
# module (500-line cap) and mount under this same prefix and scope.
from plugins.api_routers.compliance_artefacts import (  # noqa: E402
    router as _artefacts_router,
)

router.include_router(_artefacts_router)


# === Deployment posture =====================================================


@router.get("/profile")
async def compliance_profile(request: Request) -> dict[str, Any]:
    """Check the running configuration against the declared profile.

    Read-only: reports gaps with the article behind each, and never switches a
    subsystem on.
    """
    _enforce(request)
    from core.compliance.profile import evaluate_profile

    return evaluate_profile().to_dict()


@router.get("/audit/verify")
async def verify_audit_chain(request: Request) -> dict[str, Any]:
    """Verify the audit trail's hash chain (AI Act Art. 12 evidence integrity)."""
    _enforce(request)
    from core.observability.audit_setup import get_durable_audit_sink

    sink = get_durable_audit_sink()
    if sink is None:
        return {
            "ok": None,
            "reason": "no durable audit sink configured (set AUDIT_DB_PATH)",
        }
    return sink.verify_chain().to_dict()
