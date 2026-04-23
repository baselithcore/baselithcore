"""Human-in-the-loop approval routes for the Baselithbot dashboard.

Exposes the pending approval queue + decision endpoints powering the
``ComputerUseConfig.require_approval_for`` gate. A brief ``reason`` may be
attached to every decision for audit traceability.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from plugins.baselithbot.control.approvals import ApprovalStatus
from plugins.baselithbot.policies import RateLimiter
from plugins.baselithbot.dashboard.bus import _BUS
from plugins.baselithbot.dashboard.security import enforce

if TYPE_CHECKING:
    from plugins.baselithbot.plugin import BaselithbotPlugin


class ApprovalDecisionPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _enabled_capabilities(config: Any) -> list[str]:
    if not config.enabled:
        return []
    capability_flags = {
        "mouse": config.allow_mouse,
        "keyboard": config.allow_keyboard,
        "screenshot": config.allow_screenshot,
        "shell": config.allow_shell,
        "filesystem": config.allow_filesystem,
    }
    return [name for name, enabled in capability_flags.items() if enabled]


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
        computer_use = plugin.effective_computer_use_config()
        enabled_capabilities = _enabled_capabilities(computer_use)
        gated_capabilities = [
            capability
            for capability in enabled_capabilities
            if capability in set(computer_use.require_approval_for)
        ]
        visible = [*snapshot["pending"], *snapshot["history"]]
        status_counts = Counter(item["status"] for item in visible)
        capability_counts = Counter(item["capability"] for item in visible)
        action_counts = Counter(item["action"] for item in visible)
        next_expiry_ts = min(
            (item["expires_at"] for item in snapshot["pending"]),
            default=None,
        )
        latest_resolved_ts = max(
            (
                item["resolved_at"]
                for item in snapshot["history"]
                if item["resolved_at"] is not None
            ),
            default=None,
        )
        return {
            "pending": snapshot["pending"],
            "history": snapshot["history"],
            "totals": {
                "pending": len(snapshot["pending"]),
                "history": len(snapshot["history"]),
                "approved": status_counts[ApprovalStatus.APPROVED.value],
                "denied": status_counts[ApprovalStatus.DENIED.value],
                "timed_out": status_counts[ApprovalStatus.TIMED_OUT.value],
            },
            "status_counts": dict(status_counts),
            "capability_counts": dict(capability_counts),
            "action_counts": dict(action_counts),
            "oldest_pending_ts": (
                min(
                    (item["submitted_at"] for item in snapshot["pending"]), default=None
                )
            ),
            "next_expiry_ts": next_expiry_ts,
            "latest_resolved_ts": latest_resolved_ts,
            "policy": {
                "enabled": bool(gated_capabilities),
                "approval_timeout_seconds": computer_use.approval_timeout_seconds,
                "enabled_capabilities": enabled_capabilities,
                "gated_capabilities": gated_capabilities,
                "bypassed_capabilities": [
                    capability
                    for capability in enabled_capabilities
                    if capability not in set(gated_capabilities)
                ],
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
