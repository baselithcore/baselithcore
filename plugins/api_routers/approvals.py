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


class ApprovalDecision(BaseModel):
    """Reviewer decision payload for a paused run."""

    approved: bool
    approver: str | None = Field(default=None, max_length=200)
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
async def decide(run_id: str, decision: ApprovalDecision) -> dict[str, Any]:
    """Record an approve/deny decision on a paused run.

    The decision is consumed by the approval gate on the next resume: the run
    continues (approved) or aborts with a denial (denied).
    """
    store = _require_store()
    recorded = await record_approval_decision(
        store,
        run_id,
        decision.approved,
        approver=decision.approver,
        reason=decision.reason,
    )
    if not recorded:
        raise HTTPException(
            status_code=404,
            detail=f"Run '{run_id}' not found or has no pending approval.",
        )
    logger.info(
        "approval_decision_recorded run=%s approved=%s approver=%s",
        run_id,
        decision.approved,
        decision.approver,
    )
    return {"run_id": run_id, "recorded": True, "approved": decision.approved}


@router.post("/{run_id}/resume")
async def resume(run_id: str) -> dict[str, Any]:
    """Resume a checkpointed run (typically after a recorded decision).

    Completed tool steps replay from the checkpoint; the approval gate
    consumes the recorded decision and the run continues or aborts.
    """
    store = _require_store()
    checkpoint = await store.load(run_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    from core.chat import chat_service

    try:
        result = await chat_service.agent.process(
            checkpoint.query or "",
            run_id=run_id,
            resume=True,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("approval_resume_failed run=%s error=%s", run_id, exc)
        raise HTTPException(status_code=500, detail=f"Resume failed: {exc}") from exc
    return {"run_id": run_id, "result": result}
