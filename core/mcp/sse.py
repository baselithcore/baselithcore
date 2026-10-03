"""Server-Sent Events for the modern Streamable HTTP transport.

2026-07-28 gives every request its own response stream: notifications that
belong to a request (progress, logging) flow on it before the final response,
and a ``subscriptions/listen`` response stream simply stays open. There is no
shared GET stream and no resumability — a broken stream loses the request, and
the client re-issues it with a new id.

Closing the stream *is* the cancellation signal on HTTP, so the work behind a
disconnected client is cancelled rather than left running.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

SSE_MEDIA_TYPE = "text/event-stream"
# Reverse proxies buffer by default, which would hold events until the stream
# ends — exactly what a live stream must not do.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

_KEEPALIVE = ":\n\n"


def encode_event(message: dict[str, Any]) -> str:
    """Frame one JSON-RPC message as an SSE ``data:`` event."""
    return f"data: {json.dumps(message)}\n\n"


class SSEStream:
    """An outbound SSE channel driven by the handler running behind it."""

    def __init__(self, keepalive_seconds: float = 15.0) -> None:
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._keepalive = keepalive_seconds

    async def send(self, message: dict[str, Any]) -> None:
        """Queue *message* for delivery on the stream."""
        await self._queue.put(message)

    async def close(self) -> None:
        """Signal the end of the stream."""
        await self._queue.put(None)

    async def __aiter__(self) -> Any:
        """Yield SSE frames until the stream closes or the server drains.

        A comment line goes out during quiet periods: intermediaries and client
        idle timeouts drop a connection that says nothing, which on a
        long-lived subscription would look like a server crash.

        On the drain signal (the first SIGTERM/SIGINT, see
        :mod:`core.lifecycle.drain`) the frames already queued are flushed and
        the stream ends, so a subscription does not hold a rolling restart
        until the graceful-shutdown timeout. Ending the stream cancels the work
        behind it — the transport's normal cancellation signal; the client
        re-issues the request against another replica.
        """
        from core.lifecycle.drain import wait_for_drain

        drained = asyncio.ensure_future(wait_for_drain())
        getter: asyncio.Future[dict[str, Any] | None] | None = None
        try:
            while True:
                if drained.done():
                    # A busy stream must not outlive the drain either.
                    for queued in self._drain_queued():
                        yield encode_event(queued)
                    return
                getter = asyncio.ensure_future(self._queue.get())
                done, _ = await asyncio.wait(
                    {getter, drained},
                    timeout=self._keepalive,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if getter not in done:
                    # Never leave a getter on the queue: cancel it and wait for
                    # the cancellation to land. If it won the race after all,
                    # its message is delivered below rather than lost.
                    getter.cancel()
                    await asyncio.wait({getter})
                if getter.cancelled():
                    if not drained.done():
                        yield _KEEPALIVE
                    continue
                message = getter.result()
                if message is None:
                    return
                yield encode_event(message)
        finally:
            drained.cancel()
            if getter is not None:
                getter.cancel()  # consumer gone mid-wait: unhook from the queue

    def _drain_queued(self) -> list[dict[str, Any]]:
        """Messages already queued, up to an end-of-stream marker."""
        out: list[dict[str, Any]] = []
        while not self._queue.empty():
            message = self._queue.get_nowait()
            if message is None:
                break
            out.append(message)
        return out


def wants_stream(message: dict[str, Any]) -> bool:
    """Whether this request must be answered with a stream rather than a body.

    Two cases need one: a subscription, which *is* a stream, and a request that
    opted into progress, whose notifications have nowhere else to go.
    """
    if message.get("method") == "subscriptions/listen":
        return True
    params = message.get("params")
    if not isinstance(params, dict):
        return False
    meta = params.get("_meta")
    return isinstance(meta, dict) and "progressToken" in meta


__all__ = ["SSE_HEADERS", "SSE_MEDIA_TYPE", "SSEStream", "encode_event", "wants_stream"]
