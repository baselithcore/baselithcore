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

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.api.pagination import (
    PageParams,
    PaginationError,
    decode_cursor,
    encode_cursor,
    normalize_limit,
    page_params,
)
from core.models.chat import MAX_ID_LENGTH
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


#: The listing window: ``list_runs`` returns at most this many pending runs,
#: newest first. Pages are cut from that window with a keyset cursor.
_LISTING_WINDOW = 500


def _pending_entry(run_id: str, checkpoint: Any) -> dict[str, Any] | None:
    if checkpoint is None or checkpoint.status != STATUS_AWAITING_APPROVAL:
        return None
    request = dict(checkpoint.pending_approval or {})
    request.pop("decision", None)
    return {
        "run_id": run_id,
        "tenant_id": checkpoint.tenant_id,
        "query": checkpoint.query,
        "intent": checkpoint.intent,
        "pending_approval": request,
        "updated_at": checkpoint.updated_at,
    }


def _keyset(page: PageParams) -> tuple[float, str] | None:
    """The ``(updated_at, run_id)`` the previous page ended on, if any."""
    if not page.cursor:
        return None
    try:
        data = decode_cursor(page.cursor)
        return float(data["u"]), str(data["r"])
    except (PaginationError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid pagination cursor"
        ) from exc


@router.get("")
async def list_pending_approvals(
    tenant_id: str | None = Query(default=None, max_length=MAX_ID_LENGTH),
    page: PageParams = Depends(page_params),
) -> dict[str, Any]:
    """List runs durably paused awaiting a reviewer decision (cursor-paginated).

    Newest first. The cursor is a keyset on ``(updated_at, run_id)``, so a
    decision landing between two page fetches (which removes that run from
    the listing) never makes the next page skip or repeat an entry. The
    listing covers the :data:`_LISTING_WINDOW` most recent pending runs.
    """
    store = _require_store()
    limit = normalize_limit(page.limit)
    after = _keyset(page)
    rows = await store.list_runs(
        tenant_id=tenant_id, status=STATUS_AWAITING_APPROVAL, limit=_LISTING_WINDOW
    )
    if len(rows) >= _LISTING_WINDOW:
        logger.warning(
            "approvals_listing_window_full",
            extra={"window": _LISTING_WINDOW, "tenant_id": tenant_id},
        )
    keyed = sorted(
        ((float(r.get("updated_at") or 0.0), str(r.get("run_id") or "")) for r in rows),
        reverse=True,
    )
    if after is not None:
        keyed = [k for k in keyed if k < after]
    page_keys = keyed[:limit]
    has_more = len(keyed) > limit
    # Only the page is loaded, concurrently: the summaries carry no payload.
    checkpoints = await asyncio.gather(*(store.load(r) for _, r in page_keys))
    pending = [
        entry
        for (_, run_id), checkpoint in zip(page_keys, checkpoints, strict=True)
        if (entry := _pending_entry(run_id, checkpoint)) is not None
    ]
    next_cursor = (
        encode_cursor({"u": page_keys[-1][0], "r": page_keys[-1][1]})
        if has_more and page_keys
        else None
    )
    return {
        "pending": pending,
        "count": len(pending),
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


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
