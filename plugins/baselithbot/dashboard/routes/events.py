"""Dashboard live-event routes (SSE stream + recent snapshot)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from plugins.baselithbot.dashboard.bus import _BUS
from plugins.baselithbot.policies import DashboardAuth

# A comment frame goes out after this many idle seconds. Proxies and load
# balancers drop a connection that says nothing for their read timeout
# (nginx's default is 60s), and a dashboard left open on a quiet agent is
# exactly that — the EventSource would then reconnect in a loop, replaying
# the history buffer each time. Same cadence as core/mcp/sse.py.
_KEEPALIVE_SECONDS = 15.0
_KEEPALIVE_FRAME = b": keepalive\n\n"


def _frame(event: dict[str, Any]) -> bytes:
    payload = json.dumps(event)
    # Dual-emit: named frame for type-specific consumers + default "message"
    # frame so wildcard listeners (Live Logs UI) see every event regardless
    # of type.
    chunk = f"event: {event['type']}\ndata: {payload}\n\n"
    chunk += f"data: {payload}\n\n"
    return chunk.encode("utf-8")


async def _stream_frames() -> AsyncIterator[bytes]:
    """Yield SSE frames from the bus, with a keepalive during quiet periods.

    The subscription is advanced through a pending task that survives a
    timeout: ``asyncio.wait_for`` on the generator itself would cancel it
    — and so unsubscribe — on the first idle interval.
    """
    yield b": connected\n\n"
    events = _BUS.subscribe()
    pending: asyncio.Future[dict[str, Any]] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(events))
            done, _ = await asyncio.wait({pending}, timeout=_KEEPALIVE_SECONDS)
            if not done:
                yield _KEEPALIVE_FRAME
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield _frame(event)
    except asyncio.CancelledError:
        return
    finally:
        # Either path runs the bus generator's own ``finally``, which drops
        # this subscriber's queue: cancel the step in flight, or close the
        # generator where it sits suspended between events.
        if pending is not None:
            pending.cancel()
        else:
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                await aclose()


def register_events_routes(router: APIRouter, auth: DashboardAuth | None = None) -> None:
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
        return StreamingResponse(
            _stream_frames(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )


__all__ = ["register_events_routes"]
