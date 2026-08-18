"""Dashboard live-event routes (SSE stream + recent snapshot)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from plugins.baselithbot.dashboard.bus import _BUS
from plugins.baselithbot.policies import DashboardAuth


def register_events_routes(
    router: APIRouter, auth: DashboardAuth | None = None
) -> None:
    if auth is not None:

        @router.post("/events/ticket")
        async def events_ticket() -> dict[str, Any]:
            """Mint a single-use, short-lived ticket for the SSE stream.

            Sits behind the router-level bearer guard, so only authenticated
            clients can mint. EventSource cannot send headers, and putting the
            long-lived token in the query string would leak it into access
            logs — the ticket is the loggable-but-worthless stand-in.
            """
            return {
                "ticket": auth.mint_stream_ticket(),
                "ttl_seconds": 30,
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
