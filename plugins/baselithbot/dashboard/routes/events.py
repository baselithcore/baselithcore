"""Dashboard live-event routes (SSE stream + recent snapshot)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..bus import _BUS


def register_events_routes(router: APIRouter) -> None:
    @router.get("/events/recent")
    async def events_recent(limit: int = 50) -> dict[str, Any]:
        return {"events": _BUS.recent(limit=limit)}

    @router.get("/events/stream")
    async def events_stream() -> StreamingResponse:
        async def _gen() -> AsyncIterator[bytes]:
            yield b": connected\n\n"
            try:
                async for event in _BUS.subscribe():
                    payload = json.dumps(event)
                    # Dual-emit: named frame for type-specific consumers
                    # + default "message" frame so wildcard listeners (Live
                    # Logs UI) see every event regardless of type.
                    chunk = f"event: {event['type']}\ndata: {payload}\n\n"
                    chunk += f"data: {payload}\n\n"
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


__all__ = ["register_events_routes"]
