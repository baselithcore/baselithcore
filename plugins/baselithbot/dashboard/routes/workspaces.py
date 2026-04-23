"""Dashboard routes for workspace isolation (list, CRUD)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from plugins.baselithbot.policies import RateLimiter
from plugins.baselithbot.workspace import WorkspaceConfig, WorkspaceNotFoundError
from plugins.baselithbot.dashboard.bus import _BUS
from plugins.baselithbot.dashboard.schemas import WorkspaceCreateRequest, WorkspaceUpdateRequest
from plugins.baselithbot.dashboard.security import enforce

if TYPE_CHECKING:
    from plugins.baselithbot.plugin import BaselithbotPlugin


def register_workspaces_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
    delete_rate_limit: RateLimiter,
) -> None:
    @router.post("/workspaces", dependencies=[Depends(guard)])
    async def create_workspace(
        req: WorkspaceCreateRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "workspace_create")
        cfg = WorkspaceConfig(
            name=req.name,
            description=req.description,
            primary=req.primary,
            channel_overrides=req.channel_overrides,
            metadata=req.metadata,
        )
        try:
            ws = plugin.workspaces.create(cfg)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _BUS.publish("workspace.created", {"name": ws.config.name})
        return {"status": "created", "workspace": ws.runtime_summary()}

    @router.put("/workspaces/{name}", dependencies=[Depends(guard)])
    async def update_workspace(
        name: str, req: WorkspaceUpdateRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "workspace_update")
        cfg = WorkspaceConfig(
            name=name,
            description=req.description,
            primary=req.primary,
            channel_overrides=req.channel_overrides,
            metadata=req.metadata,
        )
        try:
            ws = plugin.workspaces.update(name, cfg)
        except WorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workspace not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _BUS.publish("workspace.updated", {"name": name})
        return {"status": "updated", "workspace": ws.runtime_summary()}

    @router.delete("/workspaces/{name}", dependencies=[Depends(guard)])
    async def delete_workspace(name: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "workspace_delete")
        ws = plugin.workspaces.list()
        if len(ws) <= 1 and any(w.config.name == name for w in ws):
            raise HTTPException(
                status_code=409, detail="cannot remove the last workspace"
            )
        target = next((w for w in ws if w.config.name == name), None)
        if target is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        if target.config.primary:
            raise HTTPException(
                status_code=409,
                detail="cannot remove primary workspace; promote another first",
            )
        removed = plugin.workspaces.remove(name)
        if not removed:
            raise HTTPException(status_code=404, detail="workspace not found")
        _BUS.publish("workspace.deleted", {"name": name})
        return {"status": "removed", "name": name}


__all__ = ["register_workspaces_routes"]
