"""The background memory-write backlog is bounded.

The semaphore capped concurrent writes, not the queue behind it: a sustained
burst faster than the vector store parked one task per request, without limit.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from core.orchestration.mixins import _memory_write
from core.orchestration.mixins._memory_write import (
    drain_memory_writes,
    schedule_memory_write,
)


class _BlockedMemory:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def remember(self, content: str, metadata: dict[str, Any]) -> None:
        await self.release.wait()


async def test_writes_beyond_the_backlog_bound_are_dropped(monkeypatch):
    monkeypatch.setattr(_memory_write, "_MEMORY_WRITE_MAX_BACKLOG", 5)
    memory = _BlockedMemory()
    owner = SimpleNamespace(memory_manager=memory)

    for i in range(20):
        schedule_memory_write(owner, f"q{i}", "a", "qa_docs")

    assert len(owner._memory_write_tasks) == 5
    assert owner._memory_writes_dropped == 15

    memory.release.set()
    await drain_memory_writes(owner, timeout=2.0)
    assert not owner._memory_write_tasks


async def test_drops_are_counted_and_warned_rate_limited(monkeypatch):
    """A dropped write is lost data: it must reach Prometheus and a WARNING —
    but a burst of drops must not flood the log with one line each."""
    from unittest.mock import MagicMock

    from core.observability.metrics import MEMORY_WRITES_DROPPED_TOTAL

    monkeypatch.setattr(_memory_write, "_MEMORY_WRITE_MAX_BACKLOG", 2)
    mock_logger = MagicMock()
    monkeypatch.setattr(_memory_write, "logger", mock_logger)
    clock = [1000.0]
    monkeypatch.setattr(_memory_write.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(_memory_write, "_last_drop_warning", 0.0)

    before = MEMORY_WRITES_DROPPED_TOTAL._value.get()
    memory = _BlockedMemory()
    owner = SimpleNamespace(memory_manager=memory)
    for i in range(12):
        schedule_memory_write(owner, f"q{i}", "a", "qa_docs")

    assert MEMORY_WRITES_DROPPED_TOTAL._value.get() - before == 10
    assert mock_logger.warning.call_count == 1  # 10 drops, one line

    clock[0] += _memory_write._DROP_WARNING_INTERVAL_S + 1
    schedule_memory_write(owner, "late", "a", "qa_docs")
    assert mock_logger.warning.call_count == 2

    memory.release.set()
    await drain_memory_writes(owner, timeout=2.0)
