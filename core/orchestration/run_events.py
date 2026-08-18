"""Structured run-event streaming for the agent loop.

Token-level text streaming tells a client *what* the agent is saying, not
*what it is doing*. This module adds the missing structured channel — the
equivalent of LangGraph's ``astream_events``: per-``run_id`` fan-out of
:class:`~core.api.events.AgentEvent` (run started, tool call/result, final
answer, error, human-approval pause) that a client can consume in-process via
:func:`stream_run_events` or over SSE (``GET /runs/{run_id}/events`` in the
``api_routers`` plugin).

Producers are the orchestrator execution mixin (lifecycle events, emitted
whenever a ``run_id`` is known — a checkpoint store is *not* required) and
``CheckpointManager.run_step`` (tool step events). Publishing with zero
subscribers is a no-op, so existing callers pay nothing.

Event payloads deliberately exclude tool arguments and results: they can be
large and carry tenant data. Consumers that need full state fetch it from the
checkpoint API instead.

Scope: fan-out is per-process (asyncio queues). Cross-worker delivery would
need a Redis bridge (see ``core/realtime/pubsub.py``) — not wired here.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from core.api.events import AgentEvent, EventType
from core.observability.logging import get_logger

logger = get_logger(__name__)

# Event types that end a run's stream: after one of these, no further events
# for that run_id will arrive (HUMAN_REQUEST pauses the run durably — the
# resume is a new subscription).
TERMINAL_EVENT_TYPES = frozenset(
    {EventType.RESPONSE_FINAL, EventType.ERROR, EventType.HUMAN_REQUEST}
)


class RunEventSubscription:
    """Async iterator over one run's events; also an async context manager.

    Close (or exit the ``async with`` block) to unregister — queues are only
    ever removed explicitly, so abandoning an un-closed subscription leaks it.
    """

    def __init__(
        self, stream: RunEventStream, run_id: str, queue: asyncio.Queue[AgentEvent]
    ) -> None:
        self._stream = stream
        self._run_id = run_id
        self._queue = queue

    def __aiter__(self) -> RunEventSubscription:
        return self

    async def __anext__(self) -> AgentEvent:
        return await self._queue.get()

    async def __aenter__(self) -> RunEventSubscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Unregister this subscription from the stream."""
        self._stream._unsubscribe(self._run_id, self._queue)


class RunEventStream:
    """Per-run fan-out of agent events over bounded asyncio queues.

    A slow subscriber never blocks the loop: on overflow the oldest queued
    event is dropped in favour of the new one (progress beats completeness
    for a UI feed; the checkpoint trajectory remains the complete record).
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[AgentEvent]]] = {}

    def subscribe(self, run_id: str, max_queue: int = 256) -> RunEventSubscription:
        """Register a subscriber for a run's events."""
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=max_queue)
        self._subscribers.setdefault(run_id, []).append(queue)
        return RunEventSubscription(self, run_id, queue)

    def _unsubscribe(self, run_id: str, queue: asyncio.Queue[AgentEvent]) -> None:
        queues = self._subscribers.get(run_id)
        if queues is None:
            return
        if queue in queues:
            queues.remove(queue)
        if not queues:
            del self._subscribers[run_id]

    def publish(self, run_id: str, event: AgentEvent) -> int:
        """Deliver an event to the run's subscribers; returns how many."""
        queues = self._subscribers.get(run_id)
        if not queues:
            return 0
        for queue in queues:
            if queue.full():
                try:
                    queue.get_nowait()  # drop-oldest: never block the loop
                except asyncio.QueueEmpty:  # pragma: no cover - race guard
                    pass
            queue.put_nowait(event)
        return len(queues)


_stream: RunEventStream | None = None


def get_run_event_stream() -> RunEventStream:
    """Process-wide stream shared by producers and subscribers."""
    global _stream
    if _stream is None:
        _stream = RunEventStream()
    return _stream


def reset_run_event_stream() -> None:
    """Reset the singleton (tests)."""
    global _stream
    _stream = None


def publish_run_event(
    run_id: str | None,
    event_type: EventType,
    data: dict[str, Any] | None = None,
    content: str | None = None,
) -> int:
    """Producer-side helper: build and publish one event for a run.

    No-op (returns 0) when ``run_id`` is unknown — a run nobody can address
    is a run nobody can subscribe to.
    """
    if not run_id:
        return 0
    payload = {"run_id": run_id, **(data or {})}
    event = AgentEvent(type=event_type, content=content, data=payload)
    return get_run_event_stream().publish(run_id, event)


async def stream_run_events(
    orchestrator: Any,
    query: str,
    *,
    context: dict[str, Any] | None = None,
    intent: str | None = None,
    run_id: str | None = None,
    resume: bool = False,
) -> AsyncGenerator[AgentEvent, None]:
    """Run a query and yield its structured events as they happen.

    The astream-equivalent for library users: subscribes to the run's event
    stream, launches ``orchestrator.process(...)`` concurrently, and yields
    events until a terminal one (final answer, error, or durable approval
    pause). The underlying task is always awaited, so process-level
    exceptions propagate to the caller.
    """
    rid = run_id or uuid.uuid4().hex
    stream = get_run_event_stream()
    async with stream.subscribe(rid) as subscription:
        task = asyncio.create_task(
            orchestrator.process(
                query, context=context, intent=intent, run_id=rid, resume=resume
            )
        )
        try:
            async for event in subscription:
                yield event
                if event.type in TERMINAL_EVENT_TYPES:
                    break
        finally:
            await task


__all__ = [
    "TERMINAL_EVENT_TYPES",
    "RunEventStream",
    "RunEventSubscription",
    "get_run_event_stream",
    "publish_run_event",
    "reset_run_event_stream",
    "stream_run_events",
]
