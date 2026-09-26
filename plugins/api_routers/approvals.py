"""
Human-in-the-loop approvals API.

Operator surface for the durable approval flow
(:mod:`core.orchestration.checkpoint` + ``AutonomyPolicy``): list runs paused
``awaiting_approval``, record an approve/deny decision, and resume the run so
the approval gate consumes the decision.

Mounted only when ``ORCHESTRATOR_CHECKPOINT_ENABLED`` is set (see
``ApiRoutersPlugin.get_routers``); protected by the same admin Basic Auth as
the admin router — approvals are operator actions.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.observability.logging import get_logger
from core.orchestration.checkpoint import (
    STATUS_AWAITING_APPROVAL,
    ApprovalPrincipal,
    record_approval_decision,
)
from core.orchestration.checkpoint_factory import get_default_checkpoint_store
from plugins.api_routers.admin import verify_credentials

logger = get_logger(__name__)

router = APIRouter(
    prefix="/approvals",
    tags=["approvals"],
    dependencies=[Depends(verify_credentials)],
)


#: How this router's reviewers authenticate. The whole router sits behind the
#: admin Basic Auth dependency, so every decision recorded here was made by
#: whoever holds the admin credentials — and the audit trail says so.
_AUTH_METHOD = "http_basic_admin"


class ApprovalDecision(BaseModel):
    """Reviewer decision payload for a paused run.

    ``approver`` is **not** the identity. Who decided comes from the
    authenticated request; a body field is a string the client chose, which
    answers none of the questions an auditor asks. It is kept only as an
    optional display label recorded beside the real principal.
    """

    approved: bool
    approver: str | None = Field(
        default=None,
        max_length=200,
        description="Optional display label. The recorded identity always "
        "comes from the authenticated request, never from this field.",
    )
    reason: str | None = Field(default=None, max_length=2000)


def _require_store() -> Any:
    store = get_default_checkpoint_store()
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Checkpointing is disabled "
            "(set ORCHESTRATOR_CHECKPOINT_ENABLED=true).",
        )
    return store


@router.get("")
async def list_pending_approvals(tenant_id: str | None = None) -> dict[str, Any]:
    """List runs durably paused awaiting a reviewer decision."""
    store = _require_store()
    pending: list[dict[str, Any]] = []
    for run_id in await store.list_resumable(tenant_id):
        checkpoint = await store.load(run_id)
        if checkpoint is None or checkpoint.status != STATUS_AWAITING_APPROVAL:
            continue
        request = dict(checkpoint.pending_approval or {})
        request.pop("decision", None)
        pending.append(
            {
                "run_id": run_id,
                "tenant_id": checkpoint.tenant_id,
                "query": checkpoint.query,
                "intent": checkpoint.intent,
                "pending_approval": request,
                "updated_at": checkpoint.updated_at,
            }
        )
    return {"pending": pending, "count": len(pending)}


@router.post("/{run_id}/decision")
async def decide(
    run_id: str,
    decision: ApprovalDecision,
    reviewer: str = Depends(verify_credentials),
) -> dict[str, Any]:
    """Record an approve/deny decision on a paused run.

    The decision is consumed by the approval gate on the next resume: the run
    continues (approved) or aborts with a denial (denied).

    The reviewer is the **authenticated** admin the Basic Auth dependency
    established, not anything the request body claims: an approval is a human
    taking responsibility for a side effect the policy refused to let the
    agent take alone, and an identity the caller typed is not evidence of who
    that human was.
    """
    store = _require_store()
    principal = ApprovalPrincipal(id=reviewer, auth_method=_AUTH_METHOD)
    recorded = await record_approval_decision(
        store,
        run_id,
        decision.approved,
        approver=principal,
        reason=decision.reason,
        approver_label=decision.approver,
    )
    if not recorded:
        raise HTTPException(
            status_code=404,
            detail=f"Run '{run_id}' not found or has no pending approval.",
        )
    logger.info(
        "approval_decision_recorded run=%s approved=%s approver=%s auth=%s",
        run_id,
        decision.approved,
        principal.id,
        principal.auth_method,
    )
    return {"run_id": run_id, "recorded": True, "approved": decision.approved}


@router.post("/{run_id}/resume")
async def resume(run_id: str) -> dict[str, Any]:
    """Resume a checkpointed run (typically after a recorded decision).

    Completed tool steps replay from the checkpoint; the approval gate
    consumes the recorded decision and the run continues or aborts.

    The run is re-entered under the tenant that **owns the checkpoint**, not
    the operator's ambient tenant: this route is admin-authenticated, so the
    request carries whatever tenant the admin credentials resolve to, and a
    resumed run inheriting it would read and write another tenant's memory
    and storage. Same binding the crash-recovery sweep applies.
    """
    store = _require_store()
    checkpoint = await store.load(run_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    from core.chat import chat_service
    from core.context import reset_tenant_context, set_tenant_context

    owner = checkpoint.tenant_id
    context: dict[str, Any] = {"tenant_id": owner} if owner else {}
    token = set_tenant_context(owner) if owner else None
    try:
        result = await chat_service.agent.process(
            checkpoint.query or "",
            context=context,
            run_id=run_id,
            resume=True,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("approval_resume_failed run=%s error=%s", run_id, exc)
        # The exception text can carry provider/storage internals; it stays in
        # the operator log, keyed by run id.
        raise HTTPException(status_code=500, detail="Resume failed.") from exc
    finally:
        if token is not None:
            reset_tenant_context(token)
    return {"run_id": run_id, "result": result}
