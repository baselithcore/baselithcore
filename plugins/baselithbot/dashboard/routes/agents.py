"""Dashboard routes for the multi-agent registry (list, CRUD, dispatch)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...agents import (
    ACTION_CATALOG,
    AgentActionSpec,
    CustomAgentSpec,
)
from ...policies import RateLimiter
from ..bus import _BUS
from ..schemas import (
    AgentCustomCreateRequest,
    AgentCustomUpdateRequest,
    AgentDispatchRequest,
)
from ..security import enforce

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


def _serialize_entry(
    plugin: "BaselithbotPlugin", name: str
) -> dict[str, Any] | None:
    entry = plugin.agent_registry.get(name)
    if entry is None:
        return None
    payload = entry.model_dump()
    metadata = dict(payload.get("metadata") or {})
    kind = metadata.get("kind")
    if not isinstance(kind, str):
        kind = "custom" if plugin.custom_agents.is_custom(name) else "system"
    payload["custom"] = plugin.custom_agents.is_custom(name)
    payload["kind"] = kind
    payload["metadata"] = metadata
    return payload


def register_agents_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
    delete_rate_limit: RateLimiter,
) -> None:
    @router.get("/agents")
    async def list_agents() -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for entry in plugin.agent_registry.list():
            serialized = _serialize_entry(plugin, entry.name)
            if serialized is not None:
                entries.append(serialized)
        return {
            "agents": entries,
            "name_prefix": "custom.",
            "totals": {
                "all": len(entries),
                "custom": sum(1 for e in entries if e["custom"]),
                "system": sum(1 for e in entries if not e["custom"]),
            },
        }

    @router.get("/agents/catalog")
    async def agents_catalog() -> dict[str, Any]:
        return {
            "actions": [
                {
                    "type": desc.type,
                    "label": desc.label,
                    "description": desc.description,
                    "params_schema": desc.params_schema,
                }
                for desc in ACTION_CATALOG.values()
            ],
            "name_prefix": "custom.",
        }

    @router.post("/agents", dependencies=[Depends(guard)])
    async def create_custom_agent(
        req: AgentCustomCreateRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "agent_create")
        spec = CustomAgentSpec(
            name=req.name,
            description=req.description,
            keywords=req.keywords,
            priority=req.priority,
            metadata=req.metadata,
            action=AgentActionSpec(type=req.action.type, params=req.action.params),
        )
        try:
            stored = plugin.custom_agents.register(spec)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _BUS.publish("agent.custom_registered", {"name": stored.name})
        payload = _serialize_entry(plugin, stored.name)
        return {"status": "created", "agent": payload}

    @router.put("/agents/{name}", dependencies=[Depends(guard)])
    async def update_custom_agent(
        name: str, req: AgentCustomUpdateRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "agent_update")
        if not plugin.custom_agents.is_custom(name):
            raise HTTPException(
                status_code=409, detail="system agents are immutable"
            )
        spec = CustomAgentSpec(
            name=name,
            description=req.description,
            keywords=req.keywords,
            priority=req.priority,
            metadata=req.metadata,
            action=AgentActionSpec(type=req.action.type, params=req.action.params),
        )
        try:
            stored = plugin.custom_agents.update(name, spec)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="custom agent not found"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _BUS.publish("agent.custom_updated", {"name": stored.name})
        payload = _serialize_entry(plugin, stored.name)
        return {"status": "updated", "agent": payload}

    @router.delete("/agents/{name}", dependencies=[Depends(guard)])
    async def delete_custom_agent(name: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "agent_delete")
        if not plugin.custom_agents.is_custom(name):
            raise HTTPException(
                status_code=409, detail="system agents cannot be deleted"
            )
        removed = plugin.custom_agents.delete(name)
        if not removed:
            raise HTTPException(status_code=404, detail="custom agent not found")
        _BUS.publish("agent.custom_deleted", {"name": name})
        return {"status": "removed", "name": name}

    @router.post("/agents/{name}/dispatch", dependencies=[Depends(guard)])
    async def dispatch_agent(
        name: str, req: AgentDispatchRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "agent_dispatch")
        if plugin.agent_registry.get(name) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        result = await plugin.agent_registry.invoke(name, req.query, req.context)
        _BUS.publish(
            "agent.dispatched",
            {"name": name, "status": result.get("status")},
        )
        return {"status": "dispatched", "name": name, "result": result}


__all__ = ["register_agents_routes"]
