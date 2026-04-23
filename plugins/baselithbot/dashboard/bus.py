"""Dashboard event bus — fan-out pub/sub for live dashboard events (SSE)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

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


__all__ = ["DashboardEventBus", "get_event_bus"]
