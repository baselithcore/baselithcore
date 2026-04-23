"""Dashboard routes for Live Canvas mutation + button dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from plugins.baselithbot.canvas import CanvasWidgetError, build_widgets
from plugins.baselithbot.policies import RateLimiter
from plugins.baselithbot.dashboard.bus import _BUS
from plugins.baselithbot.dashboard.schemas import CanvasDispatchRequest, CanvasRenderRequest
from plugins.baselithbot.dashboard.security import enforce

if TYPE_CHECKING:
    from plugins.baselithbot.plugin import BaselithbotPlugin


def register_canvas_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    token_rate_limit: RateLimiter,
) -> None:
    @router.post("/canvas/render", dependencies=[Depends(guard)])
    async def canvas_render(
        req: CanvasRenderRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "canvas_render")
        try:
            parsed = build_widgets(req.widgets)
        except CanvasWidgetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if req.clear:
            plugin.canvas.clear()
        plugin.canvas.extend(parsed)
        snapshot = plugin.canvas.snapshot()
        _BUS.publish(
            "canvas.rendered",
            {
                "surface_id": snapshot["surface_id"],
                "revision": snapshot["revision"],
                "added": len(parsed),
                "cleared": req.clear,
            },
        )
        return {"status": "rendered", "snapshot": snapshot}

    @router.post("/canvas/clear", dependencies=[Depends(guard)])
    async def canvas_clear(request: Request) -> dict[str, Any]:
        enforce(token_rate_limit, request, "canvas_clear")
        plugin.canvas.clear()
        snapshot = plugin.canvas.snapshot()
        _BUS.publish(
            "canvas.cleared",
            {
                "surface_id": snapshot["surface_id"],
                "revision": snapshot["revision"],
            },
        )
        return {"status": "cleared", "snapshot": snapshot}

    @router.post("/canvas/dispatch", dependencies=[Depends(guard)])
    async def canvas_dispatch(
        req: CanvasDispatchRequest, request: Request
    ) -> dict[str, Any]:
        enforce(token_rate_limit, request, "canvas_dispatch")
        event = {
            "widget_id": req.widget_id,
            "action": req.action,
            "payload": req.payload,
        }
        _BUS.publish("canvas.action", event)
        return {"status": "dispatched", **event}


__all__ = ["register_canvas_routes"]
