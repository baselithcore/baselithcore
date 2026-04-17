"""Read-only registry routes (channels, skills, crons, nodes)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...policies import RateLimiter
from ..bus import _BUS
from ..schemas import PairingTokenRequest
from ..security import enforce

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


def register_registry_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
    delete_rate_limit: RateLimiter,
) -> None:
    @router.get("/channels")
    async def list_channels() -> dict[str, Any]:
        known = plugin.channels.known()
        live = set(plugin.channels.live_names())
        inbound_stats = plugin.inbound_dispatcher.stats()
        channels: list[dict[str, Any]] = []
        for name in known:
            required = plugin.channels.required_credentials(name)
            snap = plugin.channel_configs.snapshot_entry(name, required)
            channels.append(
                {
                    "name": name,
                    "live": name in live,
                    "configured": snap["configured"],
                    "enabled": snap["enabled"],
                    "required_fields": snap["required_fields"],
                    "missing_fields": snap["missing_fields"],
                    "inbound_events": inbound_stats.get(name, 0),
                    "updated_at": snap["updated_at"],
                }
            )
        return {"channels": channels}

    @router.get("/skills")
    async def list_skills(scope: str | None = None) -> dict[str, Any]:
        skills = plugin.skills.list()
        if scope:
            skills = [s for s in skills if s.scope.value == scope]
        return {"skills": [s.model_dump(mode="json") for s in skills]}

    @router.get("/crons")
    async def list_crons() -> dict[str, Any]:
        return {
            "backend": plugin.cron.backend,
            "jobs": plugin.cron.list(),
        }

    @router.post("/crons/{name}/remove", dependencies=[Depends(guard)])
    async def remove_cron(name: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "cron_remove")
        removed = plugin.cron.remove(name)
        if not removed:
            raise HTTPException(status_code=404, detail="cron job not found")
        _BUS.publish("cron.removed", {"name": name})
        return {"status": "removed", "name": name}

    @router.get("/nodes")
    async def list_nodes() -> dict[str, Any]:
        return {
            "paired": [n.model_dump() for n in plugin.pairing.list_paired()],
            "status": plugin.pairing.status(),
        }

    @router.post("/nodes/token", dependencies=[Depends(guard)])
    async def issue_pairing_token(
        req: PairingTokenRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "node_token")
        token = plugin.pairing.issue_token(platform=req.platform)
        _BUS.publish("node.token_issued", {"platform": req.platform})
        return {"token": token, "platform": req.platform}

    @router.delete("/nodes/{node_id}", dependencies=[Depends(guard)])
    async def revoke_node(node_id: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "node_revoke")
        revoked = plugin.pairing.revoke(node_id)
        if not revoked:
            raise HTTPException(status_code=404, detail="node not paired")
        _BUS.publish("node.revoked", {"node_id": node_id})
        return {"status": "revoked", "node_id": node_id}


__all__ = ["register_registry_routes"]
