"""Fire-and-forget audit emissions are flushed, bounded, at shutdown."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from core.observability.audit import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    _pending_tasks,
    audit_emit,
    flush_pending_audit_events,
    reset_audit_logger,
    set_audit_logger,
)


class _SlowSink:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.written: list[AuditEvent] = []

    async def write(self, event: AuditEvent) -> None:
        await asyncio.sleep(self.delay)
        self.written.append(event)


@pytest.fixture(autouse=True)
def _fresh_logger() -> Iterator[None]:
    reset_audit_logger()
    yield
    reset_audit_logger()


async def test_flush_waits_for_pending_emissions() -> None:
    sink = _SlowSink(0.05)
    set_audit_logger(AuditLogger(sinks=[sink]))
    for i in range(3):
        audit_emit(AuditEventType.CUSTOM, action=f"a{i}")

    unfinished = await flush_pending_audit_events(timeout=2.0)

    assert unfinished == 0
    assert len(sink.written) == 3
    assert not _pending_tasks


async def test_flush_is_bounded_and_cancels_what_is_left() -> None:
    sink = _SlowSink(30.0)
    set_audit_logger(AuditLogger(sinks=[sink]))
    audit_emit(AuditEventType.CUSTOM, action="stuck")

    unfinished = await asyncio.wait_for(
        flush_pending_audit_events(timeout=0.05), timeout=2.0
    )

    assert unfinished == 1
    assert not _pending_tasks


async def test_flush_with_nothing_pending_returns_at_once() -> None:
    assert await flush_pending_audit_events(timeout=0.01) == 0


def test_flush_outside_a_loop_is_a_noop() -> None:
    assert asyncio.run(flush_pending_audit_events(timeout=0.01)) == 0
