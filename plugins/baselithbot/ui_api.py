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

from core.config import get_vision_config

from .doctor import run_doctor
from .metrics import is_prometheus_available, render_metrics
from .model_config import (
    KNOWN_PROVIDERS,
    KNOWN_VISION_PROVIDERS,
    ModelPreferences,
)
from .ollama_probe import fetch_ollama_catalog
from .policies import DashboardAuth, RateLimiter
from .secret_store import ALLOWED_PROVIDERS, SecretStoreError
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


class ProviderKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=512)


async def _probe_provider(provider: str, api_key: str) -> tuple[bool, str]:
    """Issue a minimal authenticated request to validate ``api_key``.

    The probe is provider-specific and cheap:
        - openai:    GET /v1/models
        - anthropic: POST /v1/messages with a 1-token prompt
        - google:    GET /v1beta/models
        - ollama:    no-op (local, no key)

    Returns ``(ok, short_detail)``. Never returns any bytes of ``api_key``.
    """
    try:
        import httpx
    except ImportError:
        return False, "httpx not installed"

    provider = provider.strip().lower()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if provider == "openai":
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                return resp.status_code == 200, f"status={resp.status_code}"
            if provider == "anthropic":
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-3-5-haiku-20241022",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
                return resp.status_code in (200, 400), f"status={resp.status_code}"
            if provider == "google":
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": api_key},
                )
                return resp.status_code == 200, f"status={resp.status_code}"
            if provider == "ollama":
                return True, "ollama is local; no remote auth"
            if provider == "huggingface":
                resp = await client.get(
                    "https://huggingface.co/api/whoami-v2",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                return resp.status_code == 200, f"status={resp.status_code}"
            return False, f"unsupported provider: {provider}"
    except httpx.HTTPError as exc:
        return False, f"network error: {type(exc).__name__}"


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

    @router.get("/run-task/latest")
    async def run_task_latest() -> dict[str, Any]:
        state = plugin.run_tracker.latest()
        return {"run": state.model_dump() if state is not None else None}

    @router.get("/run-task/recent")
    async def run_task_recent(limit: int = 8) -> dict[str, Any]:
        runs = plugin.run_tracker.recent(limit=limit)
        return {"runs": [run.model_dump() for run in runs]}

    @router.get("/run-task/{run_id}")
    async def run_task_detail(run_id: str) -> dict[str, Any]:
        state = plugin.run_tracker.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run task not found")
        return {"run": state.model_dump()}

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

    @router.get("/models")
    async def get_models() -> dict[str, Any]:
        """Expose operator-selected model prefs + catalog of known options.

        Ollama options come from a live probe of ``/api/tags`` so the picker
        reflects actually-installed local models rather than a hardcoded list.
        Probe failures silently fall back to the static catalog.
        """
        vision_cfg = get_vision_config()
        tags = await fetch_ollama_catalog(vision_cfg.ollama_url)

        llm_providers = {k: list(v) for k, v in KNOWN_PROVIDERS.items()}
        vision_providers = {k: list(v) for k, v in KNOWN_VISION_PROVIDERS.items()}
        if tags["llm"]:
            llm_providers["ollama"] = tags["llm"]
        if tags["vision"]:
            vision_providers["ollama"] = tags["vision"]

        return {
            "current": plugin.model_preferences.get().model_dump(),
            "options": {
                "llm_providers": llm_providers,
                "vision_providers": vision_providers,
            },
        }

    @router.put("/models", dependencies=[Depends(_guard)])
    async def update_models(
        prefs: ModelPreferences, request: Request
    ) -> dict[str, Any]:
        _enforce(token_rate_limit, request, "models_update")
        updated = plugin.model_preferences.update(prefs)
        plugin._apply_vision_preferences()
        _BUS.publish(
            "models.updated",
            {
                "provider": updated.provider,
                "model": updated.model,
                "vision_provider": updated.vision_provider,
                "vision_model": updated.vision_model,
            },
        )
        return {"current": updated.model_dump()}

    @router.get("/provider-keys")
    async def list_provider_keys() -> dict[str, Any]:
        """Return the configured-status snapshot (no plaintext ever)."""
        return {
            "providers": plugin.secret_store.snapshot(),
            "allowed": sorted(ALLOWED_PROVIDERS),
        }

    @router.put("/provider-keys/{provider}", dependencies=[Depends(_guard)])
    async def set_provider_key(
        provider: str,
        body: ProviderKeyRequest,
        request: Request,
    ) -> dict[str, Any]:
        _enforce(token_rate_limit, request, "provider_keys_set")
        try:
            entry = plugin.secret_store.set(provider, body.api_key)
        except SecretStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _BUS.publish(
            "provider_keys.updated",
            {"provider": entry["provider"], "last4": entry["last4"]},
        )
        return entry

    @router.delete("/provider-keys/{provider}", dependencies=[Depends(_guard)])
    async def delete_provider_key(provider: str, request: Request) -> dict[str, Any]:
        _enforce(delete_rate_limit, request, "provider_keys_delete")
        try:
            removed = plugin.secret_store.delete(provider)
        except SecretStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if removed:
            _BUS.publish(
                "provider_keys.deleted",
                {"provider": provider.strip().lower()},
            )
        return {"provider": provider.strip().lower(), "removed": removed}

    @router.post("/provider-keys/{provider}/test", dependencies=[Depends(_guard)])
    async def test_provider_key(provider: str, request: Request) -> dict[str, Any]:
        """Validate the stored key by issuing a minimal provider call."""
        _enforce(token_rate_limit, request, "provider_keys_test")
        try:
            key = plugin.secret_store.get_plaintext(provider)
        except SecretStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if key is None:
            raise HTTPException(
                status_code=404, detail="no key configured for provider"
            )
        ok, detail = await _probe_provider(provider.strip().lower(), key)
        return {"provider": provider.strip().lower(), "ok": ok, "detail": detail}

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
