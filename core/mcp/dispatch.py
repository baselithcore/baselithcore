"""Concurrent request dispatch for an MCP server connection.

Each request runs as its own task, which buys two things the spec expects and
an inline ``await handle_message(...)`` loop cannot provide:

* **Cancellation** — ``notifications/cancelled`` has something to cancel, and
  the cancelled request gets no response, as the spec requires.
* **Isolation** — one slow tool no longer holds up every other request on the
  connection.

Responses are written through the caller-supplied sender, so the same
dispatcher serves stdio and any future streaming transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from core.mcp.progress import progress_context
from core.observability.logging import get_logger

logger = get_logger(__name__)

Handler = Callable[..., Awaitable[dict[str, Any] | None]]
Sender = Callable[[dict[str, Any]], Awaitable[None]]


class RequestDispatcher:
    """Runs each incoming message as a cancellable task."""

    def __init__(self, handle: Handler, send: Sender) -> None:
        """
        Args:
            handle: Coroutine handling one JSON-RPC message, returning the
                response or None for notifications.
            send: Coroutine writing one outbound message to the client.
        """
        self._handle = handle
        self._send = send
        self._inflight: dict[Any, asyncio.Task[None]] = {}

    async def dispatch(self, message: dict[str, Any]) -> None:
        """Start serving *message*; returns without waiting for the result."""
        if message.get("method") == "notifications/cancelled":
            self._cancel(message.get("params") or {})
            return

        msg_id = message.get("id")
        task = asyncio.create_task(self._run(message, msg_id))
        if msg_id is not None:
            self._inflight[msg_id] = task
        else:
            # Notifications produce no response but still must be awaited on
            # drain, so keep them under a key that cannot collide with an id.
            self._inflight[object()] = task

    async def _run(self, message: dict[str, Any], msg_id: Any) -> None:
        token = self._progress_token(message)
        context = (token, self._send) if token is not None else None
        reset = progress_context.set(context)
        try:
            # `send` lets a long-lived request (subscriptions/listen) push
            # notifications on this connection before its final response.
            response = await self._handle(message, self._send)
            if response is not None:
                await self._send(response)
        except asyncio.CancelledError:
            # Cancelled requests get no response — the client already knows.
            logger.info("mcp_request_cancelled", request_id=msg_id)
            raise
        finally:
            progress_context.reset(reset)
            self._forget(msg_id)

    @staticmethod
    def _progress_token(message: dict[str, Any]) -> Any:
        params = message.get("params")
        if not isinstance(params, dict):
            return None
        meta = params.get("_meta")
        return meta.get("progressToken") if isinstance(meta, dict) else None

    def _cancel(self, params: dict[str, Any]) -> None:
        request_id = params.get("requestId")
        task = self._inflight.get(request_id)
        if task is None:
            # Racing a response that already went out — nothing to do.
            logger.debug("mcp_cancel_unknown_request", request_id=request_id)
            return
        logger.info(
            "mcp_cancelling_request",
            request_id=request_id,
            reason=params.get("reason"),
        )
        task.cancel()

    def _forget(self, msg_id: Any) -> None:
        if msg_id is not None:
            self._inflight.pop(msg_id, None)

    async def drain(self) -> None:
        """Wait for every in-flight request to finish or unwind."""
        while self._inflight:
            tasks = list(self._inflight.values())
            await asyncio.gather(*tasks, return_exceptions=True)
            for key, task in list(self._inflight.items()):
                if task.done():
                    self._inflight.pop(key, None)

    def cancel_all(self) -> None:
        """Cancel every in-flight request (connection teardown)."""
        for task in self._inflight.values():
            task.cancel()


__all__ = ["RequestDispatcher"]
