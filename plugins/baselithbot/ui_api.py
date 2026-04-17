"""Dashboard REST + SSE API for the Baselithbot plugin.

Exposes read/control endpoints consumed by the bundled React dashboard
served from ``plugins/baselithbot/ui/dist``. All routes live under
``/api/baselithbot/dash`` to avoid colliding with the static UI mount at
``/api/baselithbot/ui``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .doctor import run_doctor
from .metrics import is_prometheus_available, render_metrics
from .policies import DashboardAuth, RateLimiter
from .sessions.manager import SessionMessage

if TYPE_CHECKING:
    from .plugin import BaselithbotPlugin


_EVENT_BUFFER_SIZE = 200


class DashboardEventBus:
    """Fan-out pub/sub for dashboard live events (SSE).

    Keeps a bounded ring buffer so newly connected clients can replay the
    last ``_EVENT_BUFFER_SIZE`` events for context. Subscribers receive
    subsequent events via an ``asyncio.Queue``.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._history: list[dict[str, Any]] = []

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "type": event_type,
            "ts": time.time(),
            "payload": payload,
        }
        self._history.append(event)
        if len(self._history) > _EVENT_BUFFER_SIZE:
            self._history = self._history[-_EVENT_BUFFER_SIZE:]
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._history[-limit:]

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        try:
            for event in self._history[-50:]:
                await queue.put(event)
            while True:
                event = await queue.get()
                yield event
        finally:
            self._subscribers.discard(queue)


_BUS = DashboardEventBus()


def get_event_bus() -> DashboardEventBus:
    """Return the process-wide dashboard event bus."""
    return _BUS


class SessionCreateRequest(BaseModel):
    title: str = ""
    primary: bool = False


class SessionSendRequest(BaseModel):
    role: str = "user"
    content: str = Field(..., min_length=1, max_length=8000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PairingTokenRequest(BaseModel):
    platform: str | None = None


class CronToggleRequest(BaseModel):
    enabled: bool


def _client_key(request: Request, prefix: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{prefix}:{host}"


def _enforce(limiter: RateLimiter, request: Request, prefix: str) -> None:
    if not limiter.consume(_client_key(request, prefix)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")


def create_dashboard_router(
    plugin: "BaselithbotPlugin",
    auth: DashboardAuth | None = None,
) -> APIRouter:
    """Create the REST+SSE router powering the dashboard UI.

    Args:
        plugin: Owning ``BaselithbotPlugin`` instance (state source).
        auth: Optional bearer-token guard. When provided, every *write*
            endpoint requires the token; read-only endpoints stay open.
    """
    effective_auth: DashboardAuth = auth or DashboardAuth()
    router = APIRouter(prefix="/dash", tags=["Baselithbot Dashboard"])

    # Per-router rate limiters — keeps state scoped so tests and multi-mount
    # deployments do not bleed counters into each other.
    session_rate_limit = RateLimiter(window_seconds=60.0, max_events=30)
    token_rate_limit = RateLimiter(window_seconds=60.0, max_events=5)
    delete_rate_limit = RateLimiter(window_seconds=60.0, max_events=20)

    def _guard(request: Request) -> None:
        effective_auth.check(request)

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

    @router.get("/sessions")
    async def list_sessions() -> dict[str, Any]:
        return {
            "sessions": [s.model_dump() for s in plugin.sessions.list()],
        }

    @router.post("/sessions", dependencies=[Depends(_guard)])
    async def create_session(
        req: SessionCreateRequest, request: Request
    ) -> dict[str, Any]:
        _enforce(session_rate_limit, request, "session_create")
        session = plugin.sessions.create(title=req.title, primary=req.primary)
        _BUS.publish("session.created", session.model_dump())
        return session.model_dump()

    @router.get("/sessions/{sid}/history")
    async def session_history(sid: str, limit: int = 100) -> dict[str, Any]:
        if plugin.sessions.get(sid) is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session_id": sid,
            "messages": [m.model_dump() for m in plugin.sessions.history(sid, limit)],
        }

    @router.post("/sessions/{sid}/send", dependencies=[Depends(_guard)])
    async def session_send(
        sid: str, req: SessionSendRequest, request: Request
    ) -> dict[str, Any]:
        _enforce(session_rate_limit, request, "session_send")
        try:
            msg = plugin.sessions.send(
                sid,
                SessionMessage(
                    role=req.role, content=req.content, metadata=req.metadata
                ),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _BUS.publish("session.message", {"session_id": sid, **msg.model_dump()})
        return msg.model_dump()

    @router.post("/sessions/{sid}/reset", dependencies=[Depends(_guard)])
    async def session_reset(sid: str) -> dict[str, Any]:
        if plugin.sessions.get(sid) is None:
            raise HTTPException(status_code=404, detail="session not found")
        plugin.sessions.reset(sid)
        _BUS.publish("session.reset", {"session_id": sid})
        return {"status": "ok", "session_id": sid}

    @router.delete("/sessions/{sid}", dependencies=[Depends(_guard)])
    async def session_delete(sid: str, request: Request) -> dict[str, Any]:
        _enforce(delete_rate_limit, request, "session_delete")
        existed = plugin.sessions.delete(sid)
        if not existed:
            raise HTTPException(status_code=404, detail="session not found")
        _BUS.publish("session.deleted", {"session_id": sid})
        return {"status": "deleted", "session_id": sid}

    @router.get("/channels")
    async def list_channels() -> dict[str, Any]:
        known = plugin.channels.known()
        live = set(getattr(plugin.channels, "_instances", {}).keys())
        inbound_stats = plugin.inbound_dispatcher.stats()
        return {
            "channels": [
                {
                    "name": name,
                    "live": name in live,
                    "inbound_events": inbound_stats.get(name, 0),
                }
                for name in known
            ],
        }

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

    @router.post("/crons/{name}/remove", dependencies=[Depends(_guard)])
    async def remove_cron(name: str, request: Request) -> dict[str, Any]:
        _enforce(delete_rate_limit, request, "cron_remove")
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

    @router.post("/nodes/token", dependencies=[Depends(_guard)])
    async def issue_pairing_token(
        req: PairingTokenRequest, request: Request
    ) -> dict[str, Any]:
        _enforce(token_rate_limit, request, "node_token")
        token = plugin.pairing.issue_token(platform=req.platform)
        _BUS.publish("node.token_issued", {"platform": req.platform})
        return {"token": token, "platform": req.platform}

    @router.delete("/nodes/{node_id}", dependencies=[Depends(_guard)])
    async def revoke_node(node_id: str, request: Request) -> dict[str, Any]:
        _enforce(delete_rate_limit, request, "node_revoke")
        revoked = plugin.pairing.revoke(node_id)
        if not revoked:
            raise HTTPException(status_code=404, detail="node not paired")
        _BUS.publish("node.revoked", {"node_id": node_id})
        return {"status": "revoked", "node_id": node_id}

    @router.get("/doctor")
    async def doctor() -> dict[str, Any]:
        return await run_doctor()

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

    @router.get("/agents")
    async def list_agents() -> dict[str, Any]:
        return {
            "agents": [a.model_dump() for a in plugin.agent_registry.list()],
        }

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

    @router.get("/events/recent")
    async def events_recent(limit: int = 50) -> dict[str, Any]:
        return {"events": _BUS.recent(limit=limit)}

    @router.get("/events/stream")
    async def events_stream() -> StreamingResponse:
        async def _gen() -> AsyncIterator[bytes]:
            yield b": connected\n\n"
            try:
                async for event in _BUS.subscribe():
                    chunk = f"event: {event['type']}\n"
                    chunk += f"data: {json.dumps(event)}\n\n"
                    yield chunk.encode("utf-8")
            except asyncio.CancelledError:
                return

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return router


__all__ = [
    "DashboardEventBus",
    "create_dashboard_router",
    "get_event_bus",
]
