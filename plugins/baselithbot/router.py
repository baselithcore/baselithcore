"""FastAPI router for the Baselithbot plugin.

Endpoints:
    - ``POST /api/baselithbot/run`` — execute one autonomous browse task.
    - ``GET  /api/baselithbot/status`` — agent + backend status.
    - ``POST /api/baselithbot/inbound/{channel}`` — receive inbound channel
      events (Slack / Telegram / Discord / generic).
    - ``WS   /api/baselithbot/ws/pair`` — node pairing handshake.
    - ``GET  /api/baselithbot/metrics`` — Prometheus exposition.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
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
from .types import BaselithbotResult, BaselithbotTask

if TYPE_CHECKING:
    from .plugin import BaselithbotPlugin


class RunRequest(BaseModel):
    """Request body for ``POST /api/baselithbot/run``."""

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
    router = APIRouter(prefix="/baselithbot", tags=["Baselithbot"])

    @router.post("/run", response_model=BaselithbotResult)
    async def run(req: RunRequest) -> BaselithbotResult:
        agent = await plugin.get_or_start_agent()
        task = BaselithbotTask(
            goal=req.goal,
            start_url=req.start_url,
            max_steps=req.max_steps,
            extract_fields=req.extract_fields,
        )
        try:
            return await agent.execute(task)
        except Exception as exc:
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
        payload = await _read_payload(request)
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

    return router


async def _read_payload(request: Request) -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        body = await request.body()
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
