"""Dispatch inbound channel events to registered handlers."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from core.observability.logging import get_logger

logger = get_logger(__name__)


class InboundEvent(BaseModel):
    channel: str
    sender: str | None = None
    text: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    received_at: float = Field(default_factory=time.time)


InboundHandler = Callable[[InboundEvent], Awaitable[dict[str, Any]]]


class InboundDispatcher:
    """Route ``InboundEvent`` objects to per-channel handler chains."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[InboundHandler]] = {}
        self._counters: dict[str, int] = {}

    def register(self, channel: str, handler: InboundHandler) -> None:
        self._handlers.setdefault(channel, []).append(handler)

    def known_channels(self) -> list[str]:
        return sorted(self._handlers.keys())

    def stats(self) -> dict[str, int]:
        return dict(self._counters)

    async def dispatch(self, event: InboundEvent) -> list[dict[str, Any]]:
        import asyncio

        self._counters[event.channel] = self._counters.get(event.channel, 0) + 1
        chain = self._handlers.get(event.channel, [])
        if not chain:
            return [{"status": "no_handler", "channel": event.channel}]

        async def _run(handler: InboundHandler) -> dict[str, Any]:
            try:
                return await handler(event)
            except Exception as exc:
                logger.error(
                    "baselithbot_inbound_handler_error",
                    channel=event.channel,
                    error=str(exc),
                )
                return {
                    "status": "error",
                    "channel": event.channel,
                    "error": str(exc),
                }

        return list(await asyncio.gather(*(_run(h) for h in chain)))


__all__ = ["InboundDispatcher", "InboundEvent", "InboundHandler"]
