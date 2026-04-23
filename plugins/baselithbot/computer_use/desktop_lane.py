"""Per-process mutual-exclusion + cancel bookkeeping for desktop-agent runs.

Extracted from ``BaselithbotPlugin`` so the single-lane serialization (one
desktop run at a time on the host) and the cancel-event registry stay
self-contained and unit-testable.

Pattern inspired by OpenClaw's per-session lanes: one lock forces serial
execution against the shared mouse/keyboard, a dict of events lets the
dashboard (or MCP callers) signal a live run to exit at the next iteration.
"""

from __future__ import annotations

import asyncio


class DesktopLaneState:
    """Owns the desktop run lane (mutex) + per-run cancel events."""

    def __init__(self) -> None:
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._cancel_lock: asyncio.Lock = asyncio.Lock()
        self._run_lane: asyncio.Lock = asyncio.Lock()
        self._active_run_id: str | None = None

    @property
    def run_lane(self) -> asyncio.Lock:
        """Session-lane lock — serializes desktop runs on this host.

        Taking this lock forces one desktop run at a time per process so
        two concurrent goals cannot fight for the mouse/keyboard.
        """
        return self._run_lane

    def active_run_id(self) -> str | None:
        """Return the run id currently holding the desktop lane, if any."""
        return self._active_run_id

    def set_active_run(self, run_id: str | None) -> None:
        """Mark (or clear) the run id that currently owns the desktop lane."""
        self._active_run_id = run_id

    async def register_cancel(self, run_id: str) -> asyncio.Event:
        """Register (and return) a fresh cancel event for a desktop run."""
        async with self._cancel_lock:
            event = asyncio.Event()
            self._cancel_events[run_id] = event
            return event

    async def cancel_run(self, run_id: str) -> bool:
        """Signal the desktop run to stop at the next loop iteration.

        Returns ``True`` if the run was registered and the signal was
        delivered, ``False`` if no matching run is currently running.
        """
        async with self._cancel_lock:
            event = self._cancel_events.get(run_id)
            if event is None:
                return False
            event.set()
            return True

    async def clear_cancel(self, run_id: str) -> None:
        """Remove the cancel event for a finished run (idempotent)."""
        async with self._cancel_lock:
            self._cancel_events.pop(run_id, None)


__all__ = ["DesktopLaneState"]
