"""Overview, doctor, canvas, usage, agents, workspaces, prometheus routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from ...doctor import run_doctor
from ...metrics import is_prometheus_available, render_metrics

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


def register_diagnostics_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
) -> None:
    @router.get("/overview")
    async def overview() -> dict[str, Any]:
        agent = plugin.agent
        stealth_enabled = False
        backend_started = False
        agent_state = "uninitialized"
        if agent is not None:
            agent_state = agent.state.value
            stealth_enabled = agent.stealth_config.enabled
            backend_started = agent._backend is not None
        return {
            "agent": {
                "state": agent_state,
                "backend_started": backend_started,
                "stealth_enabled": stealth_enabled,
            },
            "counts": {
                "sessions": len(plugin.sessions.list()),
                "channels_registered": len(plugin.channels.known()),
                "channels_live": len(getattr(plugin.channels, "_instances", {})),
                "skills": len(plugin.skills.list()),
                "cron_jobs": len(plugin.cron.list()),
                "paired_nodes": len(plugin.pairing.list_paired()),
                "workspaces": len(plugin.workspaces.list()),
                "agents": len(plugin.agent_registry.list()),
            },
            "inbound": plugin.inbound_dispatcher.stats(),
            "usage": plugin.usage.summary(),
            "metrics_available": is_prometheus_available(),
            "cron_backend": plugin.cron.backend,
        }

    @router.get("/doctor")
    async def doctor() -> dict[str, Any]:
        return await run_doctor(plugin)

    @router.get("/canvas")
    async def canvas_snapshot() -> dict[str, Any]:
        return plugin.canvas.snapshot()

    @router.get("/usage/summary")
    async def usage_summary() -> dict[str, Any]:
        return {
            **plugin.usage.summary(),
            "by_model": plugin.usage.by_model_breakdown(),
        }

    @router.get("/usage/recent")
    async def usage_recent(limit: int = 100) -> dict[str, Any]:
        events = plugin.usage.recent(limit=limit)
        return {"events": [e.model_dump() for e in events]}

    @router.get("/workspaces")
    async def list_workspaces() -> dict[str, Any]:
        return {
            "workspaces": [w.runtime_summary() for w in plugin.workspaces.list()],
        }

    @router.get("/metrics/prometheus")
    async def prometheus_passthrough() -> dict[str, Any]:
        payload, _ = render_metrics()
        return {
            "available": is_prometheus_available(),
            "text": payload.decode("utf-8", errors="replace"),
        }


__all__ = ["register_diagnostics_routes"]
