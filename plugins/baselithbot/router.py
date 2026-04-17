"""FastAPI router for the Baselithbot plugin.

Endpoints:
    - ``POST /api/baselithbot/run`` — execute one autonomous browse task.
    - ``GET  /api/baselithbot/status`` — agent + backend status.
    - ``POST /api/baselithbot/inbound/{channel}`` — receive inbound channel
      events (Slack / Telegram / Discord / generic).
    - ``WS   /api/baselithbot/ws/pair`` — node pairing handshake.
    - ``GET  /api/baselithbot/metrics`` — Prometheus exposition.
    - ``GET  /api/baselithbot/dash/*`` — dashboard REST + SSE API.
    - ``GET  /api/baselithbot/ui/*`` — bundled React dashboard (static).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .inbound import InboundEvent
from .inbound.parsers import (
    parse_discord_interaction,
    parse_generic,
    parse_slack_event,
    parse_telegram_update,
)
from .metrics import INBOUND_EVENT_TOTAL, render_metrics
from .nodes import PairingError
from .policies import DashboardAuth, RateLimiter
from .types import BaselithbotResult, BaselithbotTask
from .ui_api import create_dashboard_router, get_event_bus

_MAX_INBOUND_BODY_BYTES = 1 * 1024 * 1024  # 1 MiB
_WS_PAIRING_RATE_LIMIT = RateLimiter(window_seconds=60.0, max_events=20)
_RUN_RATE_LIMIT = RateLimiter(window_seconds=60.0, max_events=10)

_UI_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(), camera=(), geolocation=()",
}

if TYPE_CHECKING:
    from .plugin import BaselithbotPlugin


class RunRequest(BaseModel):
    """Request body for ``POST /api/baselithbot/run``."""

    run_id: str | None = None
    goal: str = Field(..., min_length=1, max_length=4000)
    start_url: str | None = None
    max_steps: int = Field(default=20, ge=1, le=100)
    extract_fields: list[str] = Field(default_factory=list)


class StatusResponse(BaseModel):
    """Response body for ``GET /api/baselithbot/status``."""

    state: str
    backend_started: bool
    stealth_enabled: bool


def create_router(plugin: "BaselithbotPlugin") -> APIRouter:
    """Create the Baselithbot FastAPI router bound to a plugin instance."""
    router = APIRouter(tags=["Baselithbot"])
    auth = DashboardAuth()

    @router.post("/run", response_model=BaselithbotResult)
    async def run(req: RunRequest, request: Request) -> BaselithbotResult:
        auth.check(request)
        client_host = request.client.host if request.client else "unknown"
        if not _RUN_RATE_LIMIT.consume(f"run:{client_host}"):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        agent = await plugin.get_or_start_agent()
        run_id = req.run_id or f"run-{uuid4().hex[:12]}"
        task = BaselithbotTask(
            goal=req.goal,
            start_url=req.start_url,
            max_steps=req.max_steps,
            extract_fields=req.extract_fields,
        )
        plugin.run_tracker.start(
            run_id=run_id,
            goal=req.goal,
            start_url=req.start_url,
            max_steps=req.max_steps,
            extract_fields=req.extract_fields,
        )
        bus = get_event_bus()
        bus.publish(
            "run.started",
            {
                "run_id": run_id,
                "goal": req.goal,
                "max_steps": req.max_steps,
                "start_url": req.start_url,
            },
        )

        async def _on_progress(payload: dict[str, Any]) -> None:
            state = plugin.run_tracker.step(
                run_id,
                steps_taken=int(payload.get("steps_taken", 0)),
                current_url=str(payload.get("current_url", "")),
                action=str(payload.get("action", "")),
                reasoning=str(payload.get("reasoning", "")),
                history=list(payload.get("history", [])),
                extracted_data=dict(payload.get("extracted_data", {})),
                last_screenshot_b64=payload.get("last_screenshot_b64"),
            )
            if state is None:
                return
            bus.publish(
                "run.step",
                {
                    "run_id": run_id,
                    "steps_taken": state.steps_taken,
                    "action": state.last_action,
                    "reasoning": state.last_reasoning,
                    "current_url": state.current_url,
                },
            )

        try:
            result = await agent.execute(
                task,
                context={"run_id": run_id, "on_progress": _on_progress},
            )
            result.run_id = run_id
            state = plugin.run_tracker.finish(
                run_id,
                success=result.success,
                final_url=result.final_url,
                steps_taken=result.steps_taken,
                extracted_data=result.extracted_data,
                history=result.history,
                error=result.error,
                last_screenshot_b64=result.last_screenshot_b64,
            )
            bus.publish(
                "run.completed" if result.success else "run.failed",
                {
                    "run_id": run_id,
                    "steps_taken": result.steps_taken,
                    "final_url": result.final_url,
                    "error": result.error,
                    "status": state.status
                    if state is not None
                    else ("completed" if result.success else "failed"),
                },
            )
            return result
        except Exception as exc:
            plugin.run_tracker.finish(
                run_id,
                success=False,
                final_url="",
                steps_taken=0,
                extracted_data={},
                history=[],
                error=str(exc),
                last_screenshot_b64=None,
            )
            bus.publish(
                "run.failed",
                {
                    "run_id": run_id,
                    "steps_taken": 0,
                    "final_url": "",
                    "error": str(exc),
                    "status": "failed",
                },
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/status", response_model=StatusResponse)
    async def status() -> StatusResponse:
        agent = plugin.agent
        if agent is None:
            return StatusResponse(
                state="uninitialized",
                backend_started=False,
                stealth_enabled=False,
            )
        return StatusResponse(
            state=agent.state.value,
            backend_started=agent._backend is not None,
            stealth_enabled=agent.stealth_config.enabled,
        )

    @router.post("/inbound/{channel}")
    async def inbound(channel: str, request: Request) -> dict[str, Any]:
        body = await request.body()
        if len(body) > _MAX_INBOUND_BODY_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"body exceeds {_MAX_INBOUND_BODY_BYTES} bytes",
            )
        payload = _decode_payload(body)
        event = _parse_inbound(channel, payload)
        if plugin.dm_policy is not None:
            decision = plugin.dm_policy.evaluate(
                event.channel, event.sender, is_dm=False
            )
            if not decision.allowed:
                return {"status": "denied", "reason": decision.reason}
        INBOUND_EVENT_TOTAL.labels(channel=event.channel).inc()
        results = await plugin.inbound_dispatcher.dispatch(event)
        return {"status": "received", "channel": event.channel, "results": results}

    @router.websocket("/ws/pair")
    async def ws_pair(websocket: WebSocket) -> None:
        client_host = websocket.client.host if websocket.client else "unknown"
        if not _WS_PAIRING_RATE_LIMIT.consume(f"ws_pair:{client_host}"):
            await websocket.close(code=4290, reason="rate limit exceeded")
            return
        await websocket.accept()
        try:
            handshake = await websocket.receive_json()
            try:
                result = plugin.pairing.register_handshake(
                    token=handshake.get("token", ""),
                    node_id=handshake.get("node_id", ""),
                    platform=handshake.get("platform", "unknown"),
                )
            except PairingError as exc:
                await websocket.send_json({"status": "error", "error": str(exc)})
                await websocket.close(code=4000)
                return
            await websocket.send_json({"status": "paired", "node": result.model_dump()})
            while True:
                try:
                    msg = await websocket.receive_text()
                except WebSocketDisconnect:
                    break
                await websocket.send_text(f"ack: {msg[:200]}")
        except WebSocketDisconnect:
            return

    @router.get("/metrics")
    async def metrics() -> Response:
        payload, content_type = render_metrics()
        return Response(content=payload, media_type=content_type)

    @router.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/baselithbot/ui/", status_code=307)

    router.include_router(create_dashboard_router(plugin, auth=auth))
    _mount_dashboard_ui(router)

    return router


_UI_DIST = Path(__file__).resolve().parent / "ui" / "dist"


def _mount_dashboard_ui(router: APIRouter) -> None:
    """Serve the React dashboard bundle from ``plugins/baselithbot/ui/dist``.

    Built with Vite (``npm run build`` under ``ui/``). A single ``index.html``
    is served for any path under ``/ui`` that does not match a concrete
    asset so client-side routing via react-router works out of the box.
    """

    @router.get("/ui")
    async def ui_index_redirect() -> Response:
        return _serve_index()

    @router.get("/ui/")
    async def ui_index_root() -> Response:
        return _serve_index()

    @router.get("/ui/{path:path}")
    async def ui_static(path: str) -> Response:
        if path in {"", "/"}:
            return _serve_index()
        target = (_UI_DIST / path).resolve()
        try:
            target.relative_to(_UI_DIST)
        except ValueError:
            raise HTTPException(status_code=404, detail="not found") from None
        if target.is_file():
            return FileResponse(target)
        return _serve_index()


def _serve_index() -> Response:
    index = _UI_DIST / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html", headers=_UI_SECURITY_HEADERS)
    return Response(
        content=_FALLBACK_HTML,
        media_type="text/html",
        status_code=503,
        headers=_UI_SECURITY_HEADERS,
    )


_FALLBACK_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<title>Baselithbot Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<style>
body{margin:0;background:#07090d;color:#dde1ea;font-family:ui-sans-serif,system-ui,sans-serif;
display:flex;min-height:100vh;align-items:center;justify-content:center}
.card{max-width:560px;padding:32px;border:1px solid #1f242b;border-radius:16px;
background:linear-gradient(180deg,#11151b,#0d1015)}
h1{margin:0 0 12px;font-size:20px;letter-spacing:.02em}
p{margin:8px 0;color:#9aa3b2;line-height:1.5}
code{background:#151a21;padding:2px 6px;border-radius:6px;color:#c5cee0}
</style></head><body>
<div class="card">
<h1>Baselithbot Dashboard — build pending</h1>
<p>The React bundle is not built yet. Run:</p>
<p><code>cd plugins/baselithbot/ui &amp;&amp; npm install &amp;&amp; npm run build</code></p>
<p>The API remains available at <code>/api/baselithbot/dash/*</code>.</p>
</div></body></html>
"""


def _decode_payload(body: bytes) -> dict[str, Any]:
    """Decode a JSON body; fall back to ``{"raw": ...}`` on parse failure."""
    if not body:
        return {}
    try:
        import json as _json

        decoded = _json.loads(body.decode("utf-8"))
        if isinstance(decoded, dict):
            return decoded
        return {"raw_value": decoded}
    except Exception:
        return {"raw": body.decode("utf-8", errors="replace")}


def _parse_inbound(channel: str, payload: dict[str, Any]) -> InboundEvent:
    if channel == "slack":
        return parse_slack_event(payload)
    if channel == "telegram":
        return parse_telegram_update(payload)
    if channel == "discord":
        return parse_discord_interaction(payload)
    return parse_generic(channel, payload)


__all__ = ["create_router", "RunRequest", "StatusResponse"]
