"""Human-in-the-loop approval routes for the Baselithbot dashboard.

Exposes the pending approval queue + decision endpoints powering the
``ComputerUseConfig.require_approval_for`` gate. A brief ``reason`` may be
attached to every decision for audit traceability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...policies import RateLimiter
from ..bus import _BUS
from ..security import enforce

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


class ApprovalDecisionPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def register_approvals_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
) -> None:
    @router.get("/approvals")
    async def list_approvals() -> dict[str, Any]:
        """Return pending + recent-history approval requests."""
        snapshot = await plugin.approvals.snapshot()
        return {
            "pending": snapshot["pending"],
            "history": snapshot["history"],
            "totals": {
                "pending": len(snapshot["pending"]),
                "history": len(snapshot["history"]),
            },
        }

    @router.post("/approvals/{request_id}/approve", dependencies=[Depends(guard)])
    async def approve_request(
        request_id: str,
        payload: ApprovalDecisionPayload,
        request: Request,
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "approval_decision")
        ok = await plugin.approvals.approve(request_id, reason=payload.reason)
        if not ok:
            raise HTTPException(status_code=404, detail="request not pending")
        _BUS.publish(
            "approval.approved",
            {"id": request_id, "reason": payload.reason},
        )
        return {"status": "approved", "id": request_id}

    @router.post("/approvals/{request_id}/deny", dependencies=[Depends(guard)])
    async def deny_request(
        request_id: str,
        payload: ApprovalDecisionPayload,
        request: Request,
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "approval_decision")
        ok = await plugin.approvals.deny(request_id, reason=payload.reason)
        if not ok:
            raise HTTPException(status_code=404, detail="request not pending")
        _BUS.publish(
            "approval.denied",
            {"id": request_id, "reason": payload.reason},
        )
        return {"status": "denied", "id": request_id}


__all__ = ["register_approvals_routes"]
