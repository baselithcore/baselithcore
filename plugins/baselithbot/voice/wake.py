"""Voice Wake / Push-to-talk surface.

Production wake-word detection requires platform-specific listeners
(macOS Voice Control, Android continuous voice, iOS hot-word). This module
provides a uniform ``VoiceWake`` API that exposes the *state machine* and
defers actual audio capture to a pluggable backend (registered via
``set_backend``). Without a backend, the wake state stays ``IDLE`` and
all triggers must be supplied via ``trigger_external_wake``.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Awaitable, Callable

from core.observability.logging import get_logger

logger = get_logger(__name__)

WakeBackend = Callable[[], Awaitable[str]]


class WakeStatus(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRIGGERED = "triggered"
    DISABLED = "disabled"


class VoiceWake:
    """State machine for wake-word / push-to-talk activation."""

    def __init__(self) -> None:
        self._status = WakeStatus.IDLE
        self._backend: WakeBackend | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    @property
    def status(self) -> WakeStatus:
        return self._status

    def set_backend(self, backend: WakeBackend) -> None:
        """Plug in a coroutine that yields wake-phrase strings."""
        self._backend = backend

    async def trigger_external_wake(self, phrase: str = "wake") -> None:
        """Programmatic trigger (push-to-talk overlay, mobile shake, etc)."""
        await self._queue.put(phrase)
        self._status = WakeStatus.TRIGGERED
        logger.info("baselithbot_voice_wake_triggered", phrase=phrase[:80])

    async def next_wake(self, timeout: float | None = None) -> str | None:
        """Block until the next wake event or timeout returns ``None``."""
        if self._backend is None and self._queue.empty():
            self._status = WakeStatus.IDLE
            return None
        self._status = WakeStatus.LISTENING
        try:
            if self._backend is not None:
                phrase = await asyncio.wait_for(self._backend(), timeout)
            else:
                phrase = await asyncio.wait_for(self._queue.get(), timeout)
            self._status = WakeStatus.TRIGGERED
            return phrase
        except asyncio.TimeoutError:
            self._status = WakeStatus.IDLE
            return None

    def disable(self) -> None:
        self._status = WakeStatus.DISABLED


def describe_wake() -> dict[str, Any]:
    """Return a description of the wake surface for diagnostics."""
    return {
        "states": [s.value for s in WakeStatus],
        "backend_required": True,
        "external_trigger": "trigger_external_wake",
    }


__all__ = ["VoiceWake", "WakeStatus", "WakeBackend", "describe_wake"]
